# Modbus Serial Server 애플리케이션 문서

이 문서는 Modbus Serial Server 애플리케이션의 기능과 사용법에 대한 설명입니다.

## 개요
Modbus Serial Server는 Modbus 프로토콜을 통해 디바이스와 통신하는 서버 애플리케이션입니다. 이 애플리케이션을 사용하면 Modbus RTU 또는 Modbus ASCII 프로토콜을 사용하여 데이터 전송 및 수신이 가능합니다.

## 설치 방법
1. 레포지토리를 클론합니다:
   ```bash
   git clone https://github.com/nsicko42/ModbusSerialServerApp.git
   ```
2. 필요한 종속성을 설치합니다:
   ```bash
   cd ModbusSerialServerApp
   npm install
   ```

## 사용법
1. 애플리케이션을 실행합니다:
   ```bash
   node server.js
   ```
2. 서버가 실행되면, Modbus 클라이언트에서 연결할 수 있습니다.

## 기여
기여를 원하시는 경우, 이 레포지토리에 이슈를 제출하시거나 풀 리퀘스트를 생성해 주세요. 

## 라이센스
이 프로젝트는 MIT 라이센스에 따라 배포됩니다.