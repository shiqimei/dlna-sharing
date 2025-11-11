#!/usr/bin/env python3
"""
DLNA Sharing TUI Application
Stream windows to local DLNA devices (TVs)
"""

import sys
import time
import threading
import socket
import os
from typing import List, Optional, Dict, Any
from pathlib import Path
import struct
import re

# Disable all proxies for local network communication
os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'
for proxy_var in ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']:
    os.environ.pop(proxy_var, None)

# Setup debug logging to file
import sys
DEBUG_LOG = open('/tmp/dlna_app_debug.log', 'w', buffering=1)
def debug_log(msg):
    DEBUG_LOG.write(f"{msg}\n")
    DEBUG_LOG.flush()

# Check platform
if sys.platform != 'darwin':
    print("Warning: This app is optimized for macOS")

# Third-party imports
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Header, Footer, Static, ListItem, ListView, Button, Label
from textual.binding import Binding
from textual.screen import Screen
import mss
from PIL import Image
import requests

# Import streaming modules
from streaming import MJPEGStreamer, DLNAControlPoint

# macOS specific imports for window enumeration
try:
    from Quartz import (
        CGWindowListCopyWindowInfo,
        kCGWindowListOptionOnScreenOnly,
        kCGNullWindowID,
        kCGWindowName,
        kCGWindowOwnerName,
        kCGWindowNumber,
        kCGWindowBounds
    )
    MACOS_AVAILABLE = True
except ImportError:
    MACOS_AVAILABLE = False
    print("Warning: macOS window enumeration not available")


class WindowInfo:
    """Represents a window on the system"""
    def __init__(self, window_id: int, name: str, owner: str, bounds: Dict[str, float]):
        self.window_id = window_id
        self.name = name or "(Untitled)"
        self.owner = owner
        self.bounds = bounds

    def __str__(self):
        return f"{self.owner}: {self.name}"


class DLNADevice:
    """Represents a DLNA device"""
    def __init__(self, name: str, location: str, server: str = ""):
        self.name = name
        self.location = location
        self.server = server

    def __str__(self):
        return f"{self.name} - {self.server}"


def get_windows() -> List[WindowInfo]:
    """Get list of all windows on macOS"""
    if not MACOS_AVAILABLE:
        return []

    windows = []
    window_list = CGWindowListCopyWindowInfo(
        kCGWindowListOptionOnScreenOnly,
        kCGNullWindowID
    )

    for window in window_list:
        window_id = window.get(kCGWindowNumber)
        name = window.get(kCGWindowName, "")
        owner = window.get(kCGWindowOwnerName, "")
        bounds = window.get(kCGWindowBounds, {})

        # Filter out system windows and windows without names
        if owner and (name or owner != "Window Server"):
            windows.append(WindowInfo(window_id, name, owner, bounds))

    return windows


def discover_dlna_devices(timeout: int = 10, callback=None) -> List[DLNADevice]:
    """Discover DLNA devices on the local network using SSDP

    Args:
        timeout: Maximum time to wait for responses
        callback: Optional function to call immediately when a device is found.
                  Called with (device, all_devices_so_far) as arguments.
    """
    devices = []
    seen_locations = set()

    # SSDP multicast address and port
    SSDP_ADDR = "239.255.255.250"
    SSDP_PORT = 1900

    # Try multiple search targets to catch more devices
    search_targets = [
        "ssdp:all",                                          # All devices
        "upnp:rootdevice",                                   # Root devices
        "urn:schemas-upnp-org:device:MediaRenderer:1",      # Media Renderers (TVs)
        "urn:schemas-upnp-org:service:AVTransport:1",       # AVTransport service
    ]

    try:
        # Create UDP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(2)  # 2 second timeout per receive

        debug_log(f"[Discovery] Sending M-SEARCH requests...")

        # Send M-SEARCH for each search target
        for st in search_targets:
            ssdp_request = (
                'M-SEARCH * HTTP/1.1\r\n'
                f'HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n'
                'MAN: "ssdp:discover"\r\n'
                f'MX: {timeout}\r\n'
                f'ST: {st}\r\n'
                '\r\n'
            )
            sock.sendto(ssdp_request.encode('utf-8'), (SSDP_ADDR, SSDP_PORT))
            time.sleep(0.1)  # Small delay between requests

        # Receive responses
        start_time = time.time()
        response_count = 0

        while time.time() - start_time < timeout:
            try:
                data, addr = sock.recvfrom(65507)
                response = data.decode('utf-8', errors='ignore')
                response_count += 1

                # Parse response for location and server info
                location_match = re.search(r'LOCATION:\s*(.+?)[\r\n]', response, re.IGNORECASE)
                server_match = re.search(r'SERVER:\s*(.+?)[\r\n]', response, re.IGNORECASE)

                if location_match:
                    location = location_match.group(1).strip()

                    # Avoid duplicates
                    if location in seen_locations:
                        continue
                    seen_locations.add(location)

                    server = server_match.group(1).strip() if server_match else "Unknown"
                    debug_log(f"[Discovery] Found device at {addr[0]}: {location}")

                    # Try to get friendly name from device description
                    try:
                        desc_response = requests.get(location, timeout=3)
                        # Try to decode with proper encoding
                        text = desc_response.content.decode('utf-8', errors='ignore')
                        friendly_name_match = re.search(
                            r'<friendlyName>(.+?)</friendlyName>',
                            text,
                            re.IGNORECASE | re.DOTALL
                        )
                        if friendly_name_match:
                            name = friendly_name_match.group(1).strip()
                            debug_log(f"[Discovery] Device name: {name}")
                        else:
                            # Try modelName as fallback
                            model_match = re.search(
                                r'<modelName>(.+?)</modelName>',
                                text,
                                re.IGNORECASE | re.DOTALL
                            )
                            name = model_match.group(1).strip() if model_match else f"Device at {addr[0]}"
                            debug_log(f"[Discovery] Device name (from model): {name}")
                    except Exception as e:
                        debug_log(f"[Discovery] Could not fetch description from {location}: {e}")
                        name = f"Device at {addr[0]}"

                    device = DLNADevice(name, location, server)
                    devices.append(device)
                    debug_log(f"[Discovery] Added device: {name}")

                    # Call callback immediately if provided
                    if callback:
                        try:
                            callback(device, devices.copy())
                        except Exception as e:
                            debug_log(f"[Discovery] Error in callback: {e}")

            except socket.timeout:
                # Normal timeout, continue
                if time.time() - start_time < timeout:
                    continue
                else:
                    break
            except Exception as e:
                # Continue on individual response errors
                debug_log(f"[Discovery] Error processing response: {e}")
                continue

        sock.close()
        debug_log(f"[Discovery] Complete. Received {response_count} responses, found {len(devices)} unique device(s)")

    except Exception as e:
        debug_log(f"[Discovery] Error during discovery: {e}")
        import traceback
        traceback.print_exc()

    return devices


class WindowSelectionScreen(Screen):
    """Screen for selecting windows to share"""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("enter", "select_window", "Next"),
    ]

    def __init__(self, windows: List[WindowInfo]):
        super().__init__()
        self.windows = windows
        self.selected_window = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static("Select a window to share:", classes="title"),
            ListView(
                *[ListItem(Label(str(window))) for window in self.windows],
                id="window-list"
            ),
            Button("Next", variant="primary", id="next-btn"),
        )
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle window selection"""
        self.selected_window = self.windows[event.list_view.index]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press"""
        if event.button.id == "next-btn":
            self.action_select_window()

    def action_select_window(self) -> None:
        """Move to device selection screen"""
        list_view = self.query_one("#window-list", ListView)
        if list_view.index is not None:
            self.selected_window = self.windows[list_view.index]
            self.app.push_screen(DeviceSelectionScreen(self.selected_window))


class DeviceSelectionScreen(Screen):
    """Screen for selecting DLNA devices"""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("enter", "start_streaming", "Stream"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, window: WindowInfo):
        super().__init__()
        self.window = window
        self.devices = []
        self.selected_device = None
        self.discovering = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static(f"Sharing: {self.window}", classes="title"),
            Static("Discovering DLNA devices...", id="status"),
            ListView(id="device-list"),
            Button("Refresh", variant="default", id="refresh-btn"),
            Button("Start Streaming", variant="primary", id="stream-btn"),
        )
        yield Footer()

    def on_mount(self) -> None:
        """Start device discovery when screen mounts"""
        # Check if we have pre-discovered devices in cache
        with DLNAShareApp._cache_lock:
            if DLNAShareApp._device_cache:
                debug_log(f"[TUI] Using cached devices: {len(DLNAShareApp._device_cache)}")
                self.devices = DLNAShareApp._device_cache.copy()
                self.update_device_list()

        # Start fresh discovery anyway to catch any new devices
        self.discover_devices()

    def discover_devices(self) -> None:
        """Discover DLNA devices in background"""
        if self.discovering:
            debug_log("[TUI] Already discovering, skipping")
            return

        self.discovering = True
        status = self.query_one("#status", Static)
        status.update("Discovering DLNA devices...")
        debug_log("[TUI] Starting device discovery...")

        def on_device_found(device, all_devices):
            """Callback when a device is found - update UI immediately"""
            debug_log(f"[TUI] Device found callback: {device.name}")
            self.devices = all_devices
            # Update UI immediately
            self.app.call_from_thread(self.update_device_list)

        def discover():
            debug_log("[TUI] Discovery thread started")
            try:
                # Pass callback for immediate updates
                final_devices = discover_dlna_devices(timeout=5, callback=on_device_found)
                self.devices = final_devices
                debug_log(f"[TUI] Discovery complete, found {len(self.devices)} device(s)")
                # Final update
                self.app.call_from_thread(self.update_device_list)
            except Exception as e:
                debug_log(f"[TUI] Error in discovery thread: {e}")
                import traceback
                debug_log(traceback.format_exc())
            finally:
                self.discovering = False

        thread = threading.Thread(target=discover, daemon=True)
        thread.start()
        debug_log("[TUI] Discovery thread launched")

    def update_device_list(self) -> None:
        """Update the device list UI"""
        try:
            debug_log(f"[TUI] update_device_list called with {len(self.devices)} device(s)")

            status = self.query_one("#status", Static)
            device_list = self.query_one("#device-list", ListView)

            if self.devices:
                debug_log(f"[TUI] Updating status message...")
                if self.discovering:
                    status.update(f"Found {len(self.devices)} device(s)... (still searching)")
                else:
                    status.update(f"Found {len(self.devices)} device(s). Select a TV to stream:")

                debug_log(f"[TUI] Clearing device list...")
                device_list.clear()

                for i, device in enumerate(self.devices):
                    debug_log(f"[TUI] Adding device {i+1}: {device.name}")
                    device_list.append(ListItem(Label(str(device))))

                # Force refresh
                debug_log(f"[TUI] Refreshing device list...")
                device_list.refresh()
                debug_log(f"[TUI] Device list now has {len(device_list.children)} items")
            else:
                if self.discovering:
                    status.update("Searching for DLNA devices...")
                else:
                    status.update("No DLNA devices found. Make sure your TV is on and connected to the same network.")
                debug_log("[TUI] No devices found yet" if self.discovering else "[TUI] No devices found")

            debug_log("[TUI] update_device_list completed successfully")
        except Exception as e:
            debug_log(f"[TUI] Error in update_device_list: {e}")
            import traceback
            debug_log(traceback.format_exc())

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle device selection"""
        if event.list_view.index is not None and event.list_view.index < len(self.devices):
            self.selected_device = self.devices[event.list_view.index]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press"""
        debug_log(f"[TUI] Button pressed: {event.button.id}")
        if event.button.id == "refresh-btn":
            debug_log("[TUI] Refresh button clicked")
            self.action_refresh()
        elif event.button.id == "stream-btn":
            debug_log("[TUI] Stream button clicked")
            self.action_start_streaming()
        else:
            debug_log(f"[TUI] Unknown button clicked: {event.button.id}")

    def action_refresh(self) -> None:
        """Refresh device list"""
        self.discover_devices()

    def action_start_streaming(self) -> None:
        """Start streaming to selected device"""
        debug_log("[TUI] action_start_streaming called")
        list_view = self.query_one("#device-list", ListView)

        debug_log(f"[TUI] ListView index: {list_view.index}, devices count: {len(self.devices)}")

        # If no device selected but we have devices, select the first one
        if list_view.index is None and len(self.devices) > 0:
            debug_log("[TUI] No device selected, selecting first device")
            list_view.index = 0

        if list_view.index is not None and list_view.index < len(self.devices):
            self.selected_device = self.devices[list_view.index]
            debug_log(f"[TUI] Starting streaming to {self.selected_device.name}")
            status = self.query_one("#status", Static)
            status.update(f"Streaming to {self.selected_device.name}...")
            self.app.push_screen(StreamingScreen(self.window, self.selected_device))
        else:
            debug_log("[TUI] Cannot start streaming - no device selected")
            status = self.query_one("#status", Static)
            status.update("Please select a device first")


class StreamingScreen(Screen):
    """Screen showing streaming status"""

    BINDINGS = [
        Binding("escape", "stop_streaming", "Stop"),
        Binding("q", "stop_streaming", "Stop"),
    ]

    def __init__(self, window: WindowInfo, device: DLNADevice):
        super().__init__()
        self.window = window
        self.device = device
        self.streaming = False
        self.streamer = None
        self.dlna = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static(f"Window: {self.window}", classes="info"),
            Static(f"Device: {self.device}", classes="info"),
            Static("Initializing...", id="streaming-status"),
            Static("", id="stream-url"),
            Static("\nPress ESC or Q to stop streaming.", classes="help"),
            Button("Stop Streaming", variant="error", id="stop-btn"),
        )
        yield Footer()

    def on_mount(self) -> None:
        """Start streaming when screen mounts"""
        threading.Thread(target=self.start_streaming, daemon=True).start()

    def start_streaming(self) -> None:
        """Start the streaming pipeline"""
        debug_log("[Streaming] start_streaming called")

        try:
            status = self.query_one("#streaming-status", Static)
            url_widget = self.query_one("#stream-url", Static)

            # Start HLS streamer (segmented format works better with DLNA)
            debug_log("[Streaming] Starting HLS server with FFmpeg...")
            self.app.call_from_thread(status.update, "🎬 Starting video server (HLS)...")
            self.streamer = MJPEGStreamer(self.window.bounds, fps=30, port=5000, use_mpegts=False)
            self.streamer.start()
            stream_url = self.streamer.get_stream_url()
            debug_log(f"[Streaming] HLS server started at {stream_url}")

            self.app.call_from_thread(url_widget.update, f"Stream: {stream_url}")

            # Initialize DLNA control
            debug_log("[Streaming] Initializing DLNA control...")
            self.app.call_from_thread(status.update, "📡 Connecting to TV...")
            self.dlna = DLNAControlPoint(self.device.location)
            debug_log("[Streaming] DLNA control initialized")

            # Tell TV to load the stream
            debug_log("[Streaming] Sending stream URL to TV...")
            self.app.call_from_thread(status.update, "📺 Sending stream URL to TV...")
            if self.dlna.set_av_transport_uri(stream_url):
                debug_log("[Streaming] Stream URL sent successfully")
                # Start playback
                self.app.call_from_thread(status.update, "▶️  Starting playback on TV...")
                debug_log("[Streaming] Sending Play command...")
                if self.dlna.play():
                    debug_log("[Streaming] Streaming started successfully!")
                    self.streaming = True
                    self.app.call_from_thread(status.update, "🔴 Streaming active!")
                else:
                    debug_log("[Streaming] Play command failed")
                    self.app.call_from_thread(status.update, "❌ Failed to start playback on TV")
            else:
                debug_log("[Streaming] Failed to send stream URL")
                self.app.call_from_thread(status.update, "❌ Failed to send stream URL to TV")

        except Exception as e:
            debug_log(f"[Streaming] Error: {e}")
            import traceback
            debug_log(traceback.format_exc())
            self.app.call_from_thread(status.update, f"❌ Error: {e}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press"""
        if event.button.id == "stop-btn":
            self.action_stop_streaming()

    def action_stop_streaming(self) -> None:
        """Stop streaming and return to main screen"""
        status = self.query_one("#streaming-status", Static)
        status.update("⏹️  Stopping streaming...")

        # Stop DLNA playback
        if self.dlna:
            self.dlna.stop()

        # Stop streamer
        if self.streamer:
            self.streamer.stop()

        self.streaming = False
        status.update("⏹️  Streaming stopped")

        # Return to main screen
        self.app.pop_screen()
        self.app.pop_screen()
        self.app.pop_screen()


class DLNAShareApp(App):
    """Main DLNA Sharing TUI Application"""

    CSS = """
    Screen {
        align: center middle;
    }

    Container {
        width: 80;
        height: auto;
        border: solid $primary;
        padding: 1 2;
    }

    .title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
        color: $accent;
    }

    .info {
        margin-bottom: 1;
        color: $text;
    }

    .success {
        text-align: center;
        color: $success;
        text-style: bold;
    }

    .help {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }

    ListView {
        height: 15;
        margin: 1 0;
        border: solid $primary-lighten-1;
    }

    Button {
        margin-top: 1;
        width: 100%;
    }

    #status {
        margin: 1 0;
        text-align: center;
        color: $warning;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
    ]

    # Global device cache for pre-discovery
    _device_cache = []
    _cache_lock = threading.Lock()

    def __init__(self):
        super().__init__()
        self.windows = []

    def on_mount(self) -> None:
        """Initialize the app"""
        self.title = "DLNA Share"
        self.sub_title = "Stream windows to your TV"

        # Start pre-discovering devices in background immediately
        debug_log("[App] Starting pre-discovery...")
        self._start_prediscovery()

        # Get available windows
        self.windows = get_windows()

        if not self.windows:
            self.exit(message="No windows found to share. Make sure you have some applications open.")
            return

        # Show window selection screen
        self.push_screen(WindowSelectionScreen(self.windows))

    def _start_prediscovery(self) -> None:
        """Start discovering devices in the background before user needs them"""
        def prediscover():
            debug_log("[App] Pre-discovery thread started")
            try:
                def cache_device(device, all_devices):
                    """Store devices in cache as they're found"""
                    with DLNAShareApp._cache_lock:
                        DLNAShareApp._device_cache = all_devices.copy()
                    debug_log(f"[App] Cached {len(all_devices)} device(s)")

                devices = discover_dlna_devices(timeout=5, callback=cache_device)
                with DLNAShareApp._cache_lock:
                    DLNAShareApp._device_cache = devices
                debug_log(f"[App] Pre-discovery complete: {len(devices)} device(s)")
            except Exception as e:
                debug_log(f"[App] Pre-discovery error: {e}")

        thread = threading.Thread(target=prediscover, daemon=True)
        thread.start()


def main():
    """Entry point for the application"""
    print("Starting DLNA Share TUI...")
    print("Enumerating windows...")

    app = DLNAShareApp()
    app.run()


if __name__ == "__main__":
    main()
