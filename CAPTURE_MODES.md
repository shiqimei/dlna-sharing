# Capture Modes Documentation

## Overview

The DLNA sharing app supports two capture modes:

## 1. Window Capture (Default) ✅

Captures only the selected window's region instead of the entire screen.

### How It Works:
- When you select a window in the TUI, the app captures only that window's bounds (x, y, width, height)
- Uses the window's position and size from macOS Quartz API
- More focused streaming - only shows what you want to share

### Usage:
```bash
uv run dlna_sharing.py
# Select the window you want to stream
# Only that window region will be captured
```

### Example:
If you select "Slack" window at position (69, 122) with size 1112x819:
- Only that rectangular region will be captured
- Will be scaled to 720p for streaming

### Limitations:
- Captures whatever is visible at those screen coordinates
- If another window overlaps, it will be captured too
- Window must be visible (not minimized)

## 2. Monitor/Display Capture 🖥️

Captures an entire physical monitor/display.

### How It Works:
- macOS exposes physical displays as separate monitors
- Monitor 1 = Primary display
- Monitor 2 = Secondary display (if connected)
- Monitor 3+ = Additional displays

### Usage:
In `streaming.py`, you can specify which monitor to capture:

```python
# Capture primary monitor
streamer = HLSStreamer(bounds=None, monitor_index=1)

# Capture secondary monitor
streamer = HLSStreamer(bounds=None, monitor_index=2)
```

### Example Output:
```
[HLS] Available monitors: 2
[HLS]   Monitor 1: 1680x1050 at (0, 0)
[HLS]   Monitor 2: 1920x1080 at (1680, 0)
[HLS] Capturing monitor 2: 1920x1080
```

## 3. macOS Spaces (Virtual Desktops) ⚠️

### Important Limitation:
**macOS Spaces cannot be captured as separate entities.**

### Why Not:
- Spaces are virtual desktops on the same physical display
- Screen capture APIs don't expose Spaces as separate monitors
- Only the currently active Space is visible to capture

### Workarounds:

#### Option A: Switch to the Space First
1. Switch to the Space you want to capture (Ctrl+Arrow keys)
2. Run the DLNA app
3. Select a window from that Space
4. The app will capture from the active Space

#### Option B: Use Multiple Physical Monitors
1. Connect a second physical display
2. Move windows you want to capture to that display
3. Use monitor capture mode with `monitor_index=2`

#### Option C: Window Capture (Recommended)
1. Keep the window you want to stream visible
2. Use window capture mode
3. The app will capture just that window, regardless of Space

## Default Behavior

The app now defaults to **window capture** when you select a window:

```python
# In dlna_sharing.py
self.streamer = MJPEGStreamer(
    self.window.bounds,  # Uses window bounds
    fps=30,
    port=5000,
    use_mpegts=False
)
```

## Fallback Behavior

If window bounds are not available or invalid:
- Falls back to primary monitor capture
- Logs: `[HLS] Capturing full monitor (no window bounds provided)`

## Testing

### Test Window Capture:
```bash
uv run test_window_capture.py
# Shows all windows and their bounds
```

### Test Monitor Detection:
```bash
uv run dlna_sharing.py
# Check logs for "Available monitors" message
```

## Technical Details

### Window Bounds Structure:
```python
{
    'X': 69.0,       # Left position
    'Y': 122.0,      # Top position
    'Width': 1112.0,
    'Height': 819.0
}
```

### mss Capture Region:
```python
{
    "left": 69,
    "top": 122,
    "width": 1112,
    "height": 819
}
```

## Recommendations

| Use Case | Recommended Mode | Why |
|----------|-----------------|-----|
| Share specific app | Window Capture | Focused, private |
| Share entire desktop | Monitor Capture | Full screen |
| Multiple monitors | Monitor Capture (index=2+) | Select specific display |
| macOS Spaces | Window Capture | Only practical option |
| Presentation | Window Capture | Professional, focused |

## Future Enhancements

Potential improvements (not yet implemented):
- [ ] TUI option to select monitor vs window capture
- [ ] Preview of selected capture region
- [ ] Auto-follow window if it moves
- [ ] Capture multiple windows in grid layout
