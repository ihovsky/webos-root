# Как был получен root на LG webOS 26

Дата успешной установки: 24 августа 2026 года.

Проверенная конфигурация:

- телевизор: LG OLED77G6RLA;
- прошивка LG: `43.11.78`;
- webOS: webOS 26, SDK `11.1.0` (major version `11`);
- адрес телевизора во время установки: `192.168.1.11`;
- компьютер: MacBook, Python `3.9.6`;
- основа: SlopBro, commit `da374e5f10eff26b7df323ef0c2bc8470b142501`.

Результат: установлен Homebrew Channel `0.7.3`, подтверждён `Root OK`, включён SSH на порту `22` с авторизацией по отдельному ключу, Telnet выключен, приложение LG Developer Mode удалено перед последующей перезагрузкой.

## Важное ограничение

Это описание фактически выполненной процедуры на одном конкретном G6, а не официально подтверждённая универсальная инструкция для любого телевизора с webOS 26.

На 25 августа 2026 года официальный README SlopBro сообщает об успешном применении на webOS 6 и webOS 7–10/22–25. В коде commit `da374e5` уже появился предварительный target для webOS major `11` через `com.webos.app.voiceweb`, но webOS 26 ещё не включена в список официально проверенных версий.

Источники:

- SlopBro: https://github.com/throwaway96/slopbro
- Homebrew Channel: https://github.com/webosbrew/webos-homebrew-channel
- общие предупреждения webOS Homebrew: https://www.webosbrew.org/rooting/

## Что нельзя делать

- Не перезаписывать `kernel`, `rootfs`, `tvservice` и другие системные разделы.
- Не обновлять прошивку во время процедуры.
- Не перезагружать телевизор после получения root, пока установлен LG Developer Mode.
- Не включать Telnet: он не имеет нормальной аутентификации.
- Не публиковать SSAP-ключ, SSH-ключ или конфигурацию Dev Manager.
- Не считать заводской сброс полноценным откатом прошивки: он не возвращает старую версию webOS.

## 1. Проверка сети

На Mac проверялись порты телевизора:

```sh
TV_IP=192.168.1.11
nc -z -w 3 "$TV_IP" 3000
nc -z -w 3 "$TV_IP" 3001
nc -z -w 3 "$TV_IP" 9922
```

Нужна двусторонняя связь: Mac должен подключаться к ТВ, а телевизор — открывать HTTP-страницу с Mac.

Безопасная проверка штатным SlopBro:

```sh
python3 slopbro.py --test-server simple --local-ip 192.168.0.30 192.168.1.11
```

Этот режим не выполняет сопряжение, не запускает exploit и ничего не меняет на ТВ. Полученный URL надо открыть во встроенном браузере телевизора.

## 2. Подготовка SlopBro

Склонируйте этот репозиторий:

```sh
git clone https://github.com/ihovsky/webos-root.git
cd webos-root
python3 --version
```

Совместимая версия основана на upstream SlopBro commit:

```text
da374e5f10eff26b7df323ef0c2bc8470b142501
```

Оригинальная документация сохранена в `UPSTREAM_README.md`, лицензия AGPLv3 — в `COPYING`.

## 3. Почему обычный запуск не завершился

Базовый запуск для major `11` выглядел так:

```sh
python3 slopbro.py \
  --debug \
  --webos-version 11 \
  --local-ip 192.168.0.30 \
  192.168.1.11
```

На ТВ был принят запрос SSAP-сопряжения. SlopBro выбрал `com.webos.app.voiceweb` и открыл загрузочную страницу.

На прошивке `43.11.78` штатная последовательность остановилась: Download Manager скачивал первый файл, но возвращал другой формат подписанного ответа. Старый загрузчик ждал прежний признак завершения, поэтому следующие файлы не запускались. Повторные попытки оставляли Download Manager в занятом состоянии.

Если на другом webOS 26 обычный запуск полностью отработал и Homebrew уже появился, экспериментальный обход ниже не нужен.

## 4. Фактически сработавший обход webOS 26

Payload был сначала передан на ТВ через уже настроенный Developer Mode. Затем временный HTTP-сервер был поднят на самом телевизоре.

Во временную папку ТВ были переданы четыре файла:

```text
package-webos26.json
services.json
exploit-webos26.js
autoroot-webos26.sh
```

Их назначение:

- `package-webos26.json` задаёт временный пакет `com.webos.service.jsserver`;
- `services.json` регистрирует одноимённую службу;
- `exploit-webos26.js` запускает оригинальный `autoroot.sh` SlopBro;
- `autoroot-webos26.sh` совпадает с исходным `autoroot.sh` использованного commit и ставит Homebrew Channel.

Совместимая страница `run-webos26-compatible.html` выполняла следующее:

1. Отменяла зависшие загрузки через `downloadmanager/cancelAllDownloads`.
2. Последовательно, по одному, скачивала четыре файла в `/media/internal/downloads`.
3. Дожидалась фактического завершения каждого файла.
4. Вызывала `com.webos.service.jsserver/run`.

На этом G6 вызов `/run` имел разрешение только из контекста Voice Assistant. Поэтому на ТВ вручную открыли панель ассистента кнопкой микрофона/AI, а затем через SSAP запустили `com.webos.app.voiceweb` с локальным URL совместимой страницы.

Первый вызов вернул `Message not processed`. После закрытия и чистого повторного запуска Voice Assistant второй вызов вернул:

```json
{"returnValue":true}
```

После этого появился Homebrew Channel `0.7.3`, а его служба выполнялась от `uid=0`.

### Почему этот этап нельзя превращать в слепой copy-paste

Локальная страница содержала адрес конкретного ТВ `192.168.1.11:8082`, а способ передачи файлов зависел от уже настроенного Developer Mode. На другом телевизоре IP, прошивка, доступный WAM-контейнер и поведение Download Manager могут отличаться.

Если загрузка зависла после первого файла, не надо многократно перезапускать SlopBro: сначала нужно отменить зависшее задание Download Manager или полностью перезагрузить webOS.

## 5. Проверка Homebrew и root

До любой перезагрузки проверялись три независимых признака:

1. `org.webosbrew.hbchannel` присутствует в списке приложений.
2. В Homebrew Channel статус root показывает `OK`.
3. Служба Homebrew выполняет команду `id` и возвращает `uid=0`.

Если Homebrew появился, но `Root OK` отсутствует, нельзя удалять доступ Developer Mode и нельзя перезагружать телевизор до диагностики.

## 6. Безопасный SSH

На Mac был создан отдельный ключ только для телевизора:

```sh
ssh-keygen -t ed25519 -f ~/.ssh/lg-tv-root_ed25519 -C lg-tv-root
```

Публичный ключ был помещён в `/home/root/.ssh/authorized_keys`, после чего выставлены права:

```sh
chmod 700 /home/root/.ssh
chmod 600 /home/root/.ssh/authorized_keys
chown -R 0:0 /home/root/.ssh
```

Через службу Homebrew были включены SSH и запрет Telnet:

```sh
luna-send-pub -n 1 -f \
  luna://org.webosbrew.hbchannel.service/setConfiguration \
  '{"sshdEnabled":true,"telnetDisabled":true}'

luna-send-pub -n 1 -f \
  luna://org.webosbrew.hbchannel.service/autostart \
  '{"reason":"manual"}'
```

Проверка с Mac:

```sh
ssh -i ~/.ssh/lg-tv-root_ed25519 root@192.168.1.11 -p 22 'id'
```

Ожидается `uid=0`. Пароль `alpine` не использовался и не оставлялся единственным способом входа.

## 7. Удаление LG Developer Mode

SlopBro и Homebrew предупреждают: LG Developer Mode нельзя оставлять установленным при перезагрузке rooted-телевизора.

В нашем случае Developer Mode временно оставили только до подтверждения `Root OK` и SSH. Затем приложение удалили через штатный `appInstallService/remove` из root-контекста Homebrew:

```sh
luna-send -w 20000 -i \
  -a org.webosbrew.hbchannel.service \
  -f luna://com.webos.appInstallService/remove \
  '{"id":"com.palmdts.devmode","subscribe":true}'
```

После удаления проверили:

- `com.palmdts.devmode` отсутствует в списке приложений;
- `org.webosbrew.hbchannel` остаётся;
- `/var/lib/webosbrew/startup.sh` присутствует;
- SSH на порту `22` отвечает;
- Telnet на порту `23` не слушает.

Только после этих проверок телевизор можно штатно перезагружать.

## 8. Что означали странные экраны

- Чёрный экран после запуска Voice Assistant/служебной страницы был нормален; к тому моменту установка уже завершилась, и можно было нажать Home.
- Плашка «подключение к серверу нестабильно» относилась к резервному системному окну, которое не смогло открыть локальную страницу.
- Обычное выключение пультом не перезапускало webOS: `uptime` не сбрасывался. Для очистки зависшего Download Manager нужен полный reboot, но в итоге мы обошлись отменой задания и новой сессией Voice Assistant.

## Короткая схема нашего успешного пути

```text
проверка SSAP/сети → SlopBro major 11
→ обнаружена несовместимость Download Manager
→ payload локально на ТВ → Voice Assistant → jsserver /run
→ Homebrew 0.7.3 → Root OK → SSH-ключ → Telnet off
→ удалить Developer Mode → проверить persistence
```
