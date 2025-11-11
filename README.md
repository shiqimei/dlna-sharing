# DLNA Sharing TUI

A simple Terminal User Interface (TUI) application for streaming windows to DLNA devices (TVs) on your local network.

## Features

- 🖥️  **Window Selection**: Browse and select any window on your macOS system
- 📡 **DLNA Discovery**: Automatically discover DLNA-enabled TVs and devices (pre-discovers at startup)
- 🎯 **TUI Interface**: Clean, keyboard-driven terminal interface
- 🚀 **One Command**: Run with UV package manager
- ⚡ **Low Latency**: ~3-4 second latency with optimized HLS streaming
- 🎥 **720p HD**: High-quality 720p30 streaming with H.264 encoding

## Requirements

- Python 3.10+
- macOS (for window enumeration)
- UV package manager
- DLNA-enabled TV on the same network

## Installation & Usage

**Option 1: Using the run script (recommended)**

```bash
./run.sh
```

**Option 2: Direct command**

```bash
uv run dlna_sharing.py
```

That's it! UV will automatically:
- Create a virtual environment
- Install all dependencies (textual, mss, pillow, pyobjc, requests)
- Run the application

**First time setup:** Make sure you have [UV](https://github.com/astral-sh/uv) installed:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## How It Works

1. **Window Selection**: The app shows all available windows on your computer
2. **Device Discovery**: It scans your local network for DLNA devices (SSDP)
3. **HLS Streaming**: Captures screen with FFmpeg and encodes to H.264/HLS
4. **DLNA Control**: Tells your TV to play the stream (SetAVTransportURI + Play commands)

## Navigation

- **Arrow Keys**: Navigate through lists
- **Enter**: Select and proceed to next step
- **R**: Refresh device list
- **ESC**: Go back
- **Q**: Quit application

## Architecture

**Minimal implementation (~200 lines):**

1. **Window Enumeration** (`dlna_sharing.py`)
   - macOS Quartz framework to list windows

2. **DLNA Device Discovery** (`dlna_sharing.py`)
   - Custom SSDP implementation (no heavy dependencies)

3. **HLS Streaming** (`streaming.py`)
   - FFmpeg captures screen and encodes to H.264
   - Flask server serves HLS playlist and segments
   - 30 FPS, 720p, 2 Mbps for smooth playback

4. **DLNA Control Point** (`streaming.py`)
   - SOAP requests to control TV
   - `SetAVTransportURI` - set stream URL
   - `Play` - start playback

## Testing

**Test streaming without TV:**
```bash
uv run test_streaming.py
# Open http://localhost:5000/stream.m3u8 in Safari or VLC
```

**Test with actual TV:**
1. Make sure your TV is on and connected to same network
2. Run `./run.sh`
3. Select window and TV
4. Stream should appear on TV

## Streaming Format

Using **HLS (HTTP Live Streaming) with H.264**:
- ✅ Widely supported by TVs and DLNA devices
- ✅ Efficient H.264 compression
- ✅ Adaptive bitrate streaming
- ✅ Industry standard for streaming

The stream is optimized for low latency:
- 1280x720 resolution (720p HD)
- 30 FPS
- 2 Mbps bitrate
- 1-second segments (3-4 second total latency)
- H.264 Main Profile, Level 3.1
- Multi-threaded encoding for better performance

## Dependencies

**Python packages (auto-installed by UV):**
- `textual`: TUI framework
- `flask`: HTTP server for HLS streaming
- `requests`: HTTP client for DLNA SOAP requests
- `pyobjc`: macOS window enumeration
- `mss`: Fast screen capture
- `pillow`: Image processing
- `netifaces`: Network interface detection

**System requirements:**
- **FFmpeg** - Screen capture and H.264 encoding
  ```bash
  brew install ffmpeg
  ```
