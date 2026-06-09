# RunCat (Python)

A high-performance, visual macOS Menu Bar system monitor optimized for Apple Silicon and AI workflows (Ollama). 

RunCat provides real-time insights into your Mac's vital signs through a sleek menu bar animation, providing an elegant way to keep track of resource consumption without cluttering your workspace.

## ✨ Key Features

### 📊 Comprehensive System Monitoring
* **CPU & Memory:** Real-time percentage tracking with dynamic progress bars.
* **Apple Silicon GPU Support:** Deep integration via `IOKit` to monitor **Renderer** and **Tiler** utilization (AGX Accelerator), specifically for M1/M2/M3 chips.
* **Network Traffic:** Real-time upload/download speeds (KB/s, MB/s) per interface.
* **Disk Usage:** Real-time monitoring of used vs. total capacity.
* **Battery Status:** Monitoring charge percentage and remaining time.

### 🤖 AI Workload Integration (Ollama Special)
Uniquely designed for modern AI developers, RunCat can track your active Ollama session:
* **Model Tracking:** Detects the current running LLM via Ollama API.
* **VRAM & Context Info:** Displays specific model VRAM footprint and context window length in the menu bar.
* **AI Resource Isolation:** Specifically monitors CPU and Memory usage specifically belonging to `ollama` processes.

### 🎨 Advanced Visual Experience
* **Animated Menu Bar Icon:** The icon animates dynamically based on system load.
* **Real-time Performance Graph:** A custom-rendered Sparkline graph (CPU/GPU history) is injected directly into the macOS menu when opened.
* **Adaptive UI:** Interface intervals adjust automatically based on system activity to balance detail and power efficiency.

## 🛠 Tech Stack

* **GUI Framework:** `rumps` (macOS Menu Bar management).
* **Data Collection:** `psutil` (System metrics), `ctypes` & `pyobjc` (Low-level macOS IOKit/CoreFoundation access).
* **Graphics Engine:** `Pillow` (Dynamic graph rendering via `NSImage`).
* **Language:** Python 3.14+

## 🚀 Requirements & Installation

### Prerequisites
* macOS (Optimized for Apple Silicon)
* Python 3.14+

### Installation

1. **Clone the repository** (or navigate to the directory):
   ```bash
   cd /Users/baram/Project/run_cat
   ```

2. **Set up a virtual environment**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install rumps psutil Pillow pyobjc-framework-AppKit pyobjc-framework-Foundation
   ```

4. **Run the application**:
   ```bash
   python app.py
   ```

## 📝 License
This project is provided as-is for personal use and monitoring optimization.
