# RunCat Improvement Roadmap

This document outlines the strategic roadmap for evolving **RunCat** from a basic macOS system monitor into a professional-grade, high-performance dashboard for Apple Silicon and AI (LLM) workflows.

---

## 🎯 Vision
To provide an effortless, aesthetically pleasing, and highly functional window into your Mac's vital signs, specifically optimized for developers working with local Large Language Models (LLMs).

---

## 🚀 Roadmap Phases

### Phase 1: User Empowerment & Customization (Short-term)
*Goal: Transition from a "fixed" tool to a "configurable" user experience.*

#### 🎨 UX/UI Improvements
- [ ] **Configuration Menu:** Add an "Options" menu item to toggle specific metrics (e.g., hide Network, Show Battery).
- [ ] **Adaptive Themes:** Implement visual alerts (e.g., icon color changes from blue $\rightarrow$ orange $\rightarrow$ red) based on system load or temperature thresholds.
- [ ] **Display Modes:** 
    - `Compact Mode`: Only CPU & GPU in the menu bar.
    - `Standard Mode`: Default view.
    - `Advanced Mode`: Includes Network, Disk, and Ollama details.

#### ⚙️ Technical Foundations
- [ ] **Robust Error Handling:** Replace generic `try-except: pass` blocks with specific error logging and user notifications (e.g., "Ollama Connection Lost").
- [ ] **Settings Persistence:** Save user preferences in a `.json` or `.plist` file so settings persist across restarts.

---

### Phase 2: Advanced Metrics & Ecosystem Expansion (Mid-term)
*Goal: Deepen the depth of information provided and broaden the AI ecosystem support.*

#### 🌡️ System Hardware Insights
- [ ] **Thermal Monitoring:** Integrate Apple Silicon thermal sensor data to display CPU/GPU temperatures.
- [ ] **Process Drill-down:** A "Top Consumers" sub-menu showing the top 3 processes currently consuming most CPU or Memory.

#### 🤖 AI Ecosystem Integration
- [ ] **Multi-Engine Support:** Expand beyond `Ollama` to support:
    - **LM Studio** (via local server API)
    - **MLX** (direct monitoring of MLX runtime memory/compute)
    - **Local Llama.cpp** instances
- [ ] **VRAM Detailed Context:** Display more granular info about which layer or architecture is consuming VRAM when available.

---

### Phase 3: Professional Analytics & Intelligence (Long-term)
*Goal: Transform the tool from a "real-time observer" to an "analytical assistant."*

#### 📊 Data & Analytics
- [ ] **Session History:** Log system and AI workload metrics over time.
-   [ ] **Data Export:** Capability to export performance logs (CSV/JSON) for post-hoc analysis of heavy training or inference jobs.
- [ ] **Weekly Summaries:** A "Summary Report" feature showing average CPU/GPU usage and peak LLM VRAM utilization for the week.

#### 🧠 Intelligent Alerts
- [ ] **Smart Notifications:** Instead of just numbers, provide actionable insights (e.g., *"Thermal Throttling detected: High GPU usage causing temperature spike"*).
- [ ] **Predictive Monitoring:** (Advanced) Use lightweight trend estimation to warn users when a process is approaching resource exhaustion limits.

---

## 🛠 Implementation Guidelines

*   **Performance First:** All monitoring tasks MUST remain in background threads via `threading` or `asyncio`. The main thread must only handle UI updates.
*   **Low Overhead:** Monitor loops should be jitter-aware and use adaptive intervals to ensure the monitor itself doesn't cause significant CPU load.
*   **Modular Design:** Use a plugin-based architecture for new metrics (e.g., `base_monitor.py` $\rightarrow$ `ollama_monitor.py`, `thermal_monitor.py`) to allow easy extension without refactoring the core app logic.

---
*Last updated: June 09, 2026*
