# ModbusSerialServerApp

## 📋 Overview
**Modbus Serial Server** is a Python-based GUI application that implements a Modbus serial server supporting both **RTU** and **ASCII** protocols. It provides real-time monitoring and management of Modbus data blocks with an intuitive interface.

## ✨ Key Features

### 🔌 Protocol Support
- **RTU Mode**: Binary protocol format for efficient communication
- **ASCII Mode**: Text-based protocol format for easy debugging
- Configurable **Slave ID** for multi-slave environments

### 📊 Data Management
- **4 Modbus Data Blocks**:
  - Coil (Function Code 1) - Read/Write 1-bit outputs
  - Discrete Input (Function Code 2) - Read-only 1-bit inputs
  - Input Register (Function Code 4) - Read-only 16-bit registers
  - Holding Register (Function Code 3) - Read/Write 16-bit registers
- Support for up to **65,535 registers** per data block
- Real-time polling and display of register values (500ms interval)

### 🎨 User Interface
- **3-Tab Interface**:
  1. **Data Block** - Virtual scrolling display with address columns
  2. **Settings** - Serial port configuration panel
  3. **Serial Log** - Real-time RX/TX message monitoring
- **TX/RX LED Indicators** - Visual feedback for serial communication
- **Program Log** - Timestamped error and info messages

### 🔧 Configuration Options
- **Serial Port Settings**:
  - Port selection with auto-detection
  - Baud rate: 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200 bps
  - Data bits: 7 or 8
  - Parity: None, Even, Odd
  - Stop bits: 1, 1.5, or 2
  - Slave ID: Configurable per device

### 🚀 Performance Optimization
- **Virtual Scrolling**: Only visible rows are rendered (supports 6,554+ rows efficiently)
- **Asynchronous Processing**: Non-blocking UI using asyncio and threading
- **Selective Polling**: Only visible register ranges are polled to reduce overhead

### 📡 Logging & Debugging
- Color-coded serial logs (RX in blue, TX in orange)
- Timestamps with millisecond precision
- LED blink feedback on data reception/transmission
- System status display with active connection parameters

## 🔧 Technical Details

### Architecture
- **Main Component**: `ModbusServerApp` class (Tkinter-based GUI)
- **Threading Model**: Separate event loop thread for async I/O
- **Communication**: PyModbus library with serial transport
- **GUI Framework**: Tkinter with ttk widgets

### Dependencies
```python
- pymodbus >= 3.0.0
- pyserial
- tkinter (usually included with Python)
```

### Modbus Standard Implementation
- Follows Modbus specification for serial communication
- CRC error checking for RTU mode
- LRC error checking for ASCII mode
- Support for read/write operations on all standard function codes

## 📦 Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/nsicko42/ModbusSerialServerApp.git
   cd ModbusSerialServerApp
   ```

2. **Install dependencies**:
   ```bash
   pip install pymodbus>=3.0.0 pyserial
   ```

3. **Run the application**:
   ```bash
   python modbus_server.py
   ```

## 🎯 Usage Guide

### Starting the Server
1. Launch the application: `python modbus_server.py`
2. Go to **Settings** tab
3. Select your serial port from the dropdown (click "Port Refresh" to detect available ports)
4. Choose protocol mode (RTU or ASCII)
5. Configure baud rate and other serial parameters
6. Enter Slave ID (default: 1)
7. Click **[ ▶ ]** button to start the server

### Monitoring Data
1. Switch to **Data Block** tab
2. Select the data block type from inner tabs (Coil, Discrete Input, etc.)
3. Scroll to view register values
4. Values update automatically every 500ms while server is running

### Viewing Communication Logs
1. Switch to **Serial Log** tab
2. Observe RX (blue) and TX (orange) messages in real-time
3. Watch TX/RX LED indicators light up on data exchange
4. Click **Clear** to empty the log

### Stopping the Server
1. Click **[ ■ ]** button to gracefully shut down
2. Serial port will be properly released

## 🔍 System Status Indicators

| Element | Meaning |
|---------|---------|
| **Status Bar** | Shows current server state and connection parameters |
| **Info Text** | Real-time application messages with timestamps |
| **TX LED** | Lights when data is transmitted |
| **RX LED** | Lights when data is received |
| **Serial Log** | Raw protocol messages for debugging |

## 💡 Tips & Tricks

- **Port Detection**: Use "Port Refresh" button to dynamically detect newly connected serial devices
- **Performance**: For large register ranges, the virtual scrolling ensures smooth UI responsiveness
- **Debugging**: Enable Serial Log tab to troubleshoot communication issues
- **Multi-Device**: Change Slave ID to support multiple devices on the same serial line

## 📝 File Structure
```
ModbusSerialServerApp/
├── modbus_server.py          # Main application file
├── README.md                 # This file
├── LICENSE                   # MIT License
└── .gitignore               # Git ignore rules
```

## 📄 License
This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.

## 🤝 Contributing
Contributions are welcome! Feel free to:
- Report bugs and issues
- Suggest new features
- Submit pull requests with improvements

## 📮 Support
For questions, issues, or feature requests, please open an issue on the GitHub repository.

---

**Created**: April 2026  
**Language**: Python 3.8+  
**Platform**: Windows, macOS, Linux
