# Bottled Memray (Memray Web Studio)

A production-grade web-based memory profiling studio for Python powered by Bloomberg's [Memray](https://github.com/bloomberg/memray), packaged for **Cloud in a Bottle**.

## ✨ Features
- **Interactive Python Playground**: Write or paste Python code directly in your browser and profile allocations in one click.
- **Built-in Demos**: Quickstart with memory leak simulations, Pandas DataFrame merge spikes, eager lists vs. lazy generators, and deep recursive call trees.
- **Interactive Flame Graph**: Visualizes call stacks with zoomable, searchable SVG/D3 flame graphs.
- **Detailed Memory Allocation Table**: Inspect total memory, own memory, and allocation counts per function.
- **Metrics & Distribution**: Peak memory footprint, total allocation count, runtime duration, and allocator type breakdowns.
- **Profile File Uploader**: Drag & drop `.bin` capture files recorded from remote servers or CI tests for immediate web visualization.
- **Profiling History**: Persistent storage of past profiling runs in `$BOTTLE_APP_DATA_DIR`.

## 🔒 Security
- **Owner Authentication**: Protected by Cloud in a Bottle's reverse-proxy zero-trust gateway (`public_paths = []`). Only the authenticated owner of the instance can execute code or inspect memory profiles.
- **Rootless Execution**: Runs under unprivileged user `UID 10001` (`memrayuser`).

## 📦 Resources
- Memory: 1024 MB
- CPU: 1.0 Core
- Port: 8080
