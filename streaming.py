"""
Simple HLS streaming using Python screenshots + FFmpeg encoding
This avoids FFmpeg's avfoundation permission issues
"""

import time
import threading
import socket
import os
import subprocess
from pathlib import Path
from io import BytesIO
from flask import Flask, send_from_directory
import requests
import re
from PIL import Image
import mss

# Disable all proxies
os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'
for proxy_var in ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']:
    os.environ.pop(proxy_var, None)


class HLSStreamer:
    """HLS streamer using Python screenshots + FFmpeg encoding"""

    def __init__(self, bounds: dict = None, fps: int = 15, port: int = 5000, use_mpegts: bool = False, monitor_index: int = 1):
        self.bounds = bounds or {}
        self.fps = fps
        self.port = port
        self.running = False
        self.ffmpeg_process = None
        self.server_thread = None
        self.capture_thread = None
        self.use_mpegts = use_mpegts  # If True, use raw MPEG-TS instead of HLS
        self.monitor_index = monitor_index  # Which monitor to capture (1=primary, 2=secondary, etc.)

        # HLS output directory
        self.hls_dir = Path("/tmp/dlna_hls")
        self.hls_dir.mkdir(exist_ok=True)

        # Clean up old segments
        for f in self.hls_dir.glob("*.ts"):
            f.unlink()
        for f in self.hls_dir.glob("*.m3u8"):
            f.unlink()

        # Setup Flask
        self.app = Flask(__name__)

        if self.use_mpegts:
            # For MPEG-TS mode, serve the stream file directly
            @self.app.route('/stream.ts')
            def serve_mpegts():
                response = send_from_directory(str(self.hls_dir), 'stream.ts', mimetype='video/mp2t')
                # Add DLNA headers
                response.headers['transferMode.dlna.org'] = 'Streaming'
                response.headers['contentFeatures.dlna.org'] = 'DLNA.ORG_PN=MPEG_TS_SD_NA;DLNA.ORG_OP=01;DLNA.ORG_FLAGS=01700000000000000000000000000000'
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response

        @self.app.route('/<path:filename>')
        def serve_hls(filename):
            from flask import Response
            import os

            file_path = self.hls_dir / filename

            # For .ts segments, use chunked transfer encoding for ultra-low latency
            if filename.endswith('.ts'):
                def generate():
                    """Stream file in chunks for LL-HLS"""
                    try:
                        chunk_size = 8192  # 8KB chunks
                        with open(file_path, 'rb') as f:
                            while True:
                                chunk = f.read(chunk_size)
                                if not chunk:
                                    break
                                yield chunk
                    except Exception as e:
                        print(f"[HLS] Error streaming {filename}: {e}")

                response = Response(generate(), mimetype='video/mp2t')
                response.headers['Transfer-Encoding'] = 'chunked'
                response.headers['Cache-Control'] = 'no-cache'
            else:
                # For playlist files, serve normally
                response = send_from_directory(str(self.hls_dir), filename)
                if filename.endswith('.m3u8'):
                    response.headers['Content-Type'] = 'application/vnd.apple.mpegurl'
                    response.headers['Cache-Control'] = 'no-cache'

            # Add DLNA and CORS headers
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['transferMode.dlna.org'] = 'Streaming'
            return response

        import logging
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)

    def start(self):
        """Start streaming"""
        if self.running:
            return

        self.running = True

        # Start Flask server
        self.server_thread = threading.Thread(
            target=lambda: self.app.run(host='0.0.0.0', port=self.port, threaded=True),
            daemon=True
        )
        self.server_thread.start()
        time.sleep(1)
        print(f"[HLS] Flask server started on port {self.port}")

        # Start FFmpeg to encode stdin to HLS or MPEG-TS
        # 720p HD resolution - good balance of quality and performance
        width = 1280
        height = 720

        if self.use_mpegts:
            # Use raw MPEG-TS format - 720p low latency
            ffmpeg_cmd = [
                'ffmpeg',
                '-y',
                '-f', 'rawvideo',
                '-pixel_format', 'rgb24',
                '-video_size', f'{width}x{height}',
                '-framerate', str(self.fps),
                '-i', 'pipe:0',  # Read from stdin
                '-c:v', 'libx264',
                '-profile:v', 'main',
                '-level', '3.1',
                '-preset', 'veryfast',
                '-tune', 'zerolatency',
                '-b:v', '2M',
                '-maxrate', '2M',
                '-bufsize', '1M',
                '-pix_fmt', 'yuv420p',
                '-g', str(self.fps),
                '-sc_threshold', '0',
                '-threads', '0',
                '-f', 'mpegts',
                str(self.hls_dir / 'stream.ts')
            ]
        else:
            # Use HLS with H.264 - ULTRA LOW LATENCY 720p settings
            # Based on LL-HLS techniques: partial segments, flush packets, reduced GOP
            ffmpeg_cmd = [
                'ffmpeg',
                '-y',
                # Format flags for ultra-low latency
                '-fflags', '+flush_packets+nobuffer',  # Flush packets immediately, no buffering
                '-flags', '+low_delay',  # Enable low delay mode
                '-f', 'rawvideo',
                '-pixel_format', 'rgb24',
                '-video_size', f'{width}x{height}',
                '-framerate', str(self.fps),
                '-i', 'pipe:0',  # Read from stdin
                # Video codec settings
                '-c:v', 'libx264',
                '-profile:v', 'main',  # Main profile for better quality
                '-level', '3.1',  # Level 3.1 for 720p30
                '-preset', 'veryfast',  # Fast encoding
                '-tune', 'zerolatency',  # Zero latency tuning (adds only 0.5s)
                '-b:v', '2M',  # 2Mbps for 720p
                '-maxrate', '2M',
                '-bufsize', '500k',  # Very small buffer for ultra-low latency
                '-pix_fmt', 'yuv420p',
                '-g', str(int(self.fps * 0.5)),  # Keyframe every 0.5 seconds (even faster seeking)
                '-sc_threshold', '0',  # Disable scene change detection
                '-threads', '0',  # Use all CPU cores
                # Ultra-low latency muxer settings
                '-max_delay', '0',  # No muxer delay
                '-muxdelay', '0',  # No mux delay
                # LL-HLS settings
                '-f', 'hls',
                '-hls_time', '0.5',  # 0.5 second segments (LL-HLS partial segments)
                '-hls_list_size', '4',  # Keep 4 segments (2 seconds buffer)
                '-hls_flags', 'delete_segments+omit_endlist+independent_segments',  # LL-HLS flags
                '-hls_segment_type', 'mpegts',
                '-start_number', '0',
                '-hls_segment_filename', str(self.hls_dir / 'segment_%03d.ts'),
                str(self.hls_dir / 'stream.m3u8')
            ]

        print(f"[HLS] Starting FFmpeg encoder...")
        self.ffmpeg_process = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Start capture thread
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()

        # Wait for stream to be ready
        print(f"[HLS] Waiting for stream...")
        for i in range(5):
            time.sleep(1)
            if (self.hls_dir / 'stream.m3u8').exists():
                print(f"[HLS] Stream ready!")
                break

        if self.use_mpegts:
            print(f"[MPEG-TS] Stream URL: http://0.0.0.0:{self.port}/stream.ts")
        else:
            print(f"[HLS] Stream URL: http://0.0.0.0:{self.port}/stream.m3u8")

    def _capture_loop(self):
        """Capture screenshots and pipe to FFmpeg - OPTIMIZED for low latency"""
        with mss.mss() as sct:
            width, height = 1280, 720  # 720p HD
            target_time = 1.0 / self.fps

            # List available monitors
            print(f"[HLS] Available monitors: {len(sct.monitors) - 1}")  # -1 because monitors[0] is all monitors
            for i, monitor in enumerate(sct.monitors):
                if i > 0:  # Skip monitors[0] which is all monitors combined
                    print(f"[HLS]   Monitor {i}: {monitor['width']}x{monitor['height']} at ({monitor['left']}, {monitor['top']})")

            # Determine capture region
            if self.bounds and all(k in self.bounds for k in ['X', 'Y', 'Width', 'Height']):
                # Capture specific window bounds
                capture_region = {
                    "left": int(self.bounds['X']),
                    "top": int(self.bounds['Y']),
                    "width": int(self.bounds['Width']),
                    "height": int(self.bounds['Height'])
                }
                print(f"[HLS] Capturing window region: {capture_region['width']}x{capture_region['height']} at ({capture_region['left']}, {capture_region['top']})")
            else:
                # Capture specified monitor (or primary if out of range)
                if self.monitor_index < len(sct.monitors):
                    capture_region = sct.monitors[self.monitor_index]
                    print(f"[HLS] Capturing monitor {self.monitor_index}: {capture_region['width']}x{capture_region['height']}")
                else:
                    capture_region = sct.monitors[1]
                    print(f"[HLS] Monitor {self.monitor_index} not found, using primary monitor")

            while self.running and self.ffmpeg_process and self.ffmpeg_process.poll() is None:
                try:
                    frame_start = time.time()

                    # Capture screenshot from specified region
                    screenshot = sct.grab(capture_region)
                    img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

                    # Resize to target resolution - use BILINEAR for speed (30% faster than LANCZOS)
                    img = img.resize((width, height), Image.BILINEAR)

                    # Write raw RGB to FFmpeg stdin (no flush - better performance)
                    self.ffmpeg_process.stdin.write(img.tobytes())

                    # Maintain FPS with more accurate timing
                    elapsed = time.time() - frame_start
                    sleep_time = target_time - elapsed
                    if sleep_time > 0.001:  # Only sleep if significant time remains
                        time.sleep(sleep_time)

                except Exception as e:
                    print(f"[HLS] Capture error: {e}")
                    break

    def stop(self):
        """Stop streaming"""
        self.running = False

        if self.ffmpeg_process:
            try:
                self.ffmpeg_process.stdin.close()
                self.ffmpeg_process.terminate()
                self.ffmpeg_process.wait(timeout=5)
            except:
                pass
            print("[HLS] FFmpeg stopped")

        # Clean up
        for f in self.hls_dir.glob("*.ts"):
            f.unlink()
        for f in self.hls_dir.glob("*.m3u8"):
            f.unlink()

        print("[HLS] Stopped")

    def get_stream_url(self) -> str:
        """Get stream URL using the correct local network interface"""
        import netifaces

        # Get all network interfaces
        local_ip = '127.0.0.1'

        try:
            # Get all interfaces with IPv4 addresses
            for interface in netifaces.interfaces():
                addrs = netifaces.ifaddresses(interface)
                if netifaces.AF_INET in addrs:
                    for addr_info in addrs[netifaces.AF_INET]:
                        ip = addr_info.get('addr')
                        # Skip loopback and proxy IPs (198.18.x.x range)
                        if ip and not ip.startswith('127.') and not ip.startswith('198.18.'):
                            # Prefer 192.168.x.x addresses (common local network)
                            if ip.startswith('192.168.'):
                                local_ip = ip
                                break
                            # Fallback to any non-loopback IP
                            elif local_ip == '127.0.0.1':
                                local_ip = ip
                if local_ip != '127.0.0.1' and local_ip.startswith('192.168.'):
                    break
        except Exception as e:
            print(f"[HLS] Error detecting IP: {e}, using fallback")
            # Fallback to old method but with better filtering
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(('8.8.8.8', 80))
                detected_ip = s.getsockname()[0]
                if not detected_ip.startswith('198.18.'):
                    local_ip = detected_ip
            except:
                pass
            finally:
                s.close()

        print(f"[HLS] Using IP address: {local_ip}")

        if self.use_mpegts:
            return f"http://{local_ip}:{self.port}/stream.ts"
        else:
            return f"http://{local_ip}:{self.port}/stream.m3u8"


# For backward compatibility
MJPEGStreamer = HLSStreamer
class DLNAControlPoint:
    """Minimal DLNA Control Point to control Media Renderers (TVs)"""

    def __init__(self, device_location: str):
        self.device_location = device_location
        self.control_url = None
        self._fetch_control_url()

    def _fetch_control_url(self):
        """Get the AVTransport control URL from device description"""
        try:
            response = requests.get(self.device_location, timeout=5)

            # Parse for AVTransport service control URL
            match = re.search(
                r'<serviceType>urn:schemas-upnp-org:service:AVTransport:.*?</serviceType>.*?'
                r'<controlURL>(.*?)</controlURL>',
                response.text,
                re.DOTALL | re.IGNORECASE
            )

            if match:
                control_path = match.group(1)
                # Make absolute URL
                from urllib.parse import urljoin
                self.control_url = urljoin(self.device_location, control_path)
                print(f"[DLNA] Control URL: {self.control_url}")
            else:
                print("[DLNA] Could not find AVTransport control URL")
        except Exception as e:
            print(f"[DLNA] Error fetching control URL: {e}")

    def _send_soap_request(self, action: str, arguments: dict) -> bool:
        """Send SOAP request to device"""
        if not self.control_url:
            print("[DLNA] No control URL available")
            return False

        # Build SOAP envelope
        args_xml = ''.join(f'<{k}>{v}</{k}>' for k, v in arguments.items())

        soap_body = f'''<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
            s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:{action} xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">
      <InstanceID>0</InstanceID>
      {args_xml}
    </u:{action}>
  </s:Body>
</s:Envelope>'''

        headers = {
            'Content-Type': 'text/xml; charset="utf-8"',
            'SOAPAction': f'"urn:schemas-upnp-org:service:AVTransport:1#{action}"',
        }

        try:
            response = requests.post(
                self.control_url,
                data=soap_body,
                headers=headers,
                timeout=10
            )
            print(f"[DLNA] {action} response: {response.status_code}")
            return response.status_code == 200
        except Exception as e:
            print(f"[DLNA] Error sending {action}: {e}")
            return False

    def set_av_transport_uri(self, stream_url: str) -> bool:
        """Tell the TV where the media is located"""
        print(f"[DLNA] Setting stream URL: {stream_url}")

        # Detect format and set appropriate protocol info
        if stream_url.endswith('.ts'):
            # MPEG-TS format - more universally compatible
            protocol_info = "http-get:*:video/mp2t:*"
            mime_type = "video/mp2t"
        else:
            # HLS format
            protocol_info = "http-get:*:application/vnd.apple.mpegurl:*"
            mime_type = "application/vnd.apple.mpegurl"

        # Create proper DIDL-Lite metadata for video stream
        # This tells the TV what kind of content to expect
        metadata = '''&lt;DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"
xmlns:dc="http://purl.org/dc/elements/1.1/"
xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"&gt;
&lt;item id="0" parentID="-1" restricted="1"&gt;
&lt;dc:title&gt;Screen Share&lt;/dc:title&gt;
&lt;res protocolInfo="{}"&gt;{}&lt;/res&gt;
&lt;upnp:class&gt;object.item.videoItem&lt;/upnp:class&gt;
&lt;/item&gt;
&lt;/DIDL-Lite&gt;'''.format(protocol_info, stream_url)

        print(f"[DLNA] Using protocol info: {protocol_info}")
        return self._send_soap_request('SetAVTransportURI', {
            'CurrentURI': stream_url,
            'CurrentURIMetaData': metadata
        })

    def play(self) -> bool:
        """Tell the TV to start playback"""
        print("[DLNA] Sending Play command")
        return self._send_soap_request('Play', {
            'Speed': '1'
        })

    def stop(self) -> bool:
        """Tell the TV to stop playback"""
        print("[DLNA] Sending Stop command")
        return self._send_soap_request('Stop', {})
