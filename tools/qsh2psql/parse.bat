@echo off
setlocal enabledelayedexpansion
set BASE_DIR=G:\backup\history\qscalp\erinrv.qscalp.ru

REM Установка кодовой страницы UTF-8
chcp 65001 >nul

REM Получение пути к директории, где находится bat-скрипт
set SCRIPT_DIR=%~dp0

REM Проверка наличия основного аргумента (start_date)
if "%~1"=="" (
    echo Ошибка: Не указана начальная дата
    exit /b 1
)

REM Установка начальной и конечной даты
set start_date=%~1
set end_date=%start_date%

REM Обход файлов .qsh в папке

set folder=%BASE_DIR%\%start_date%
rem echo Обработка: %start_date%

pushd "%folder%"
for %%f in (*.qsh) do (
    rem echo Обработка файла: %%f

    REM Создание временного .txt файла с помощью qsh2txt.exe
    set qsh_file=%%f

    rem echo Создание временного файла: !txt_file!
    qsh2txt.exe "!qsh_file!" >nul 2>nul

    set txt_file=%%~nf.txt
    ren %%~nf*.txt !txt_file!

    REM Проверка имени файла и вызов соответствующего Python-скрипта
    echo %%f | findstr /i "AuxInfo" >nul
    if !errorlevel! equ 0 (
        echo !txt_file!
        python "%SCRIPT_DIR%qsh_auxinfo.py" "!txt_file!"
    ) else (
        echo %%f | findstr /i "Deals" >nul
        if !errorlevel! equ 0 (
            echo !txt_file!
            python "%SCRIPT_DIR%qsh_deals.py" "!txt_file!"
        ) else (
            echo %%f | findstr /i "Quotes" >nul
            if !errorlevel! equ 0 (
                echo !txt_file!
                python "%SCRIPT_DIR%qsh_quotes.py" "!txt_file!"
            ) else (
                echo Неизвестный тип файла: %%f
            )
        )
    )

    REM Удаление временного .txt файла
    rem echo Удаление временного файла: !txt_file!
    del "!txt_file!"
)
popd
