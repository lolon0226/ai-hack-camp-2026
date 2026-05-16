# RedRibbon Demo — PDFCreator 가상프린터 (redribbon)

본선 시연은 **C:\RedRibbonDemo** 전용 Print Receiver 를 사용합니다.

## PDFCreator 설정

1. PDFCreator에서 프린터 이름 **redribbon** 을 사용합니다.
2. **자동 저장(Auto-Save)** 폴더를 아래로 지정합니다.

   `C:\RedRibbonDemo\incoming`

3. 병원 EMR·OCS에서 인쇄 대상으로 **redribbon** 을 선택합니다.
4. 저장된 PDF는 Print Receiver가 감시하여 래드리본 서버로 업로드합니다.

## 폴더 구조 (설치 후)

```
C:\RedRibbonDemo\
  print_receiver\     receiver_engine.py, config.json
  incoming\           PDFCreator 자동저장
  uploading\          업로드 처리 중
  uploaded\           업로드 완료
  failed\             업로드 실패
  logs\               receiver.log
  run_redribbon_receiver.ps1
```

## 실행

```powershell
C:\RedRibbonDemo\run_redribbon_receiver.ps1
```

또는:

```powershell
python C:\RedRibbonDemo\print_receiver\receiver_engine.py --config C:\RedRibbonDemo\print_receiver\config.json
```
