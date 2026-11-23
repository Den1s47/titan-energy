# Скрипт автозапуска «Титан-Энергия» (Windows) — специф §12.

Запуск всего приложения одной командой:
    cd backend && uv run uvicorn titan.api.app:app --host 127.0.0.1 --port 8000

## Вариант 1: Task Scheduler (планировщик Windows)

Создать задачу «TitanEnergy»:
1. Win+R -> taskschd.msc -> «Создать задачу...»
2. Имя: TitanEnergy
3. Триггер: «При входе в систему» (или «При запуске системы» для службы)
4. Действие:
   - Программа:  `C:\Users\HP\.local\bin\uv.exe`
   - Аргументы:  `run uvicorn titan.api.app:app --host 127.0.0.1 --port 8000`
   - Рабочая папка: `C:\Users\HP\HermesProjects\titan-energy\backend`
5. Галочка «Запускать с наивысшими правами» не нужна (порт > 1024)

### Задача ночного бэкапа «TitanBackup»:
- Триггер: ежедневно, 03:00
- Действие:
  - Программа:  `C:\Users\HP\.local\bin\uv.exe`
  - Аргументы:  `run python -m titan.backup_cli --keep 7`
  - Рабочая папка: `C:\Users\HP\HermesProjects\titan-energy\backend`

## Вариант 2: NSSM (Windows-служба) — для постоянной работы

1. Скачать nssm.exe, положить в PATH
2. В cmd от администратора:
   ```
   nssm install TitanEnergy "C:\Users\HP\.local\bin\uv.exe" "run uvicorn titan.api.app:app --host 127.0.0.1 --port 8000"
   nssm set TitanEnergy AppDirectory C:\Users\HP\HermesProjects\titan-energy\backend
   nssm start TitanEnergy
   ```
3. Удалить службу: `nssm remove TitanEnergy confirm`

## Бэкап вручную

```
cd backend
uv run python -m titan.backup_cli --list            # список копий
uv run python -m titan.backup_cli                   # создать копию + ротация
uv run python -m titan.backup_cli --restore titan_20261121_120000.db
```
Копии лежат в `backend/backups/`, хранятся 7 последних (настраивается --keep).

## Firewall (LAN-доступ с других устройств, специф §11)

PowerShell от администратора (только приватный профиль!):
```
New-NetFirewallRule -DisplayName "TitanEnergy Web" -Direction Inbound `
  -LocalPort 8000 -Protocol TCP -Profile Private -Action Allow
```
После этого система доступна с других устройств LAN по http://<IP-ноутбука>:8000.
IP узнать: `ipconfig` (адрес Wi-Fi/Ethernet адаптера).
