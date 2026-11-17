Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\Vlaslav\Downloads\GitHub_Control_Panel"
WshShell.Run """C:\Users\Vlaslav\AppData\Local\Programs\Python\Python312\pythonw.exe"" -X utf8 -u ""C:\Users\Vlaslav\Downloads\GitHub_Control_Panel\office_seo_worker.py"" --startup --then-start-poller", 0, False
