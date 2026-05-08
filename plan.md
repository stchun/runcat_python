# Performance Analysis & Optimization Plan

Current performance issues in `app.py` compared to the native RunCat application are primarily due to high resource overhead from Python-level abstractions and frequent system-level operations.

## 1. Identified Performance Bottlenecks

### 1.1 High-Cost Subprocess Calls
- **Issue:** `get_gpu_stats` executes `ioreg` every second using `subprocess.run`.
- **Impact:** Spawning a new process is an expensive operation in terms of CPU and energy. It involves process creation, execution, and output parsing (regex).
- **Comparison:** Native apps use C/Swift APIs to access system metrics directly from memory.

### 1.2 Expensive Process Scanning
- **Issue:** `OllamaProcessTracker.update` calls `psutil.process_iter` every second.
- **Impact:** Iterating over every running process on the system is extremely heavy. As the number of processes increases, CPU usage spikes, preventing the CPU from entering low-power states.

### 1.3 Inefficient Disk I/O (Graph Rendering)
- **Issue:** `HistoryGraph.render_nsimage` saves a PNG file to disk (`img.save`) and then reads it back via `NSImage`.
- **Impact:** Writing to and reading from the SSD every second consumes significant power and contributes to SSD wear.
- **Comparison:** Native apps render graphics directly in memory (bitmaps/layers).

### 1.4 High-Frequency Timer & GIL
- **Issue:** `_animate` runs on a 0.02s (50 FPS) timer.
- **Impact:** Even if no frame update is needed, the timer wakes up the Python interpreter. Python's Global Interpreter Lock (GIL) and timer overhead prevent the CPU from staying in deep idle states.

### 1.5 Frequent Network Polling
- **Issue:** `get_ollama_api_info` performs an HTTP request to the Ollama API every second.
- **Impact:** Constant socket activity and HTTP protocol overhead keep the network stack active.

---

## 2. Optimization Strategy

### Phase 1: High-Impact Fixes (Immediate Battery Savings)
- [x] **Memory-based Rendering:** Replace disk-based PNG saving with `io.BytesIO` to pass image data directly to `NSImage` via `NSData`.
- [x] **Optimized Process Tracking:** Cache the Ollama PID and only re-scan the process list if the cached process is no longer active.
- [x] **Reduce Polling Frequency:** Increase the interval for non-critical stats like Disk and Ollama API.

### Phase 2: Structural Improvements
- [ ] **Dynamic Animation Timer:** Adjust the timer interval itself based on system load, rather than running a high-frequency timer that checks conditions.
- [ ] **Direct GPU Access:** Investigate using `PyObjC` to access `IOKit` frameworks directly for GPU metrics, avoiding `ioreg` subprocess calls.

### Phase 3: Resource Management
- [ ] **Thread Efficiency:** Consolidate background tasks to minimize context switching.
- [ ] **Adaptive Polling:** Slow down all polling when the laptop is on battery or when the menu is closed (if detectable).
