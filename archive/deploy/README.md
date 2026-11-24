# Новый VPN-узел на Ubuntu

Сценарий рассчитан на чистый Ubuntu 24.04 LTS с uv, Docker Compose v2 и свободным TCP/443. Он поднимает автономный Xray. У действующего AdminVPS свой 3x-ui, поэтому для переноса из панели нужны отдельный экспорт профилей и проверка перед переключением клиентов.

## Подготовка

1. Добавьте публичный SSH-ключ для входа на VPS и проверьте отпечаток хоста через консоль провайдера.
2. Выдайте VPS отдельный SSH deploy key с доступом на чтение приватного репозитория либо склонируйте репозиторий своим настроенным способом.
3. Откройте TCP/443 в сетевом экране провайдера. Для SSH оставьте доступ с административных адресов, если сеть провайдера это позволяет.

```bash
sudo apt update
sudo apt install git python3 openssl docker.io docker-compose-v2
sudo systemctl enable --now docker
git clone git@github.com:gutsy51/vpn.git
cd vpn
uv sync --locked --no-dev
install -m 600 .env.deploy.example .env.deploy
```

В `.env.deploy` укажите REALITY target и SNI, соответствующий сертификату цели. Для нового узла сгенерируйте отдельную пару X25519, short ID и три UUID. Версию образа берите из `compose.yaml`. Команды выводят данные для настройки в терминал:

```bash
image=$(sudo docker compose --env-file /dev/null config --images)
sudo docker run --rm "$image" x25519
openssl rand -hex 8
python3 -c 'import uuid; print(*(uuid.uuid4() for _ in range(3)), sep="\n")'
```

Приватный ключ внесите в `XRAY_REALITY_PRIVATE_KEY`. Второе значение из генератора Xray - клиентский ключ для профилей REALITY, в редакторах он может называться `Public Key` или `Password`. Сохраните его вместе с резервной копией параметров, чтобы потом выдать профиль новому устройству.

## Первый запуск

```bash
uv run --locked --no-dev vpn-manager container deploy
```

Скрипт собирает конфигурацию из `.env.deploy`, валидирует её самим Xray и запускает контейнер. Если на этом узле уже был сгенерированный конфиг, его предыдущая версия сохраняется в `.local/xray.previous.json`. Снаружи публикуется TCP/443, внутри контейнера Xray слушает TCP/8443.

На каждом устройстве создайте отдельный профиль VLESS на адресе нового VPS и порту 443. Транспорт TCP/RAW, защита REALITY, flow `xtls-rprx-vision`. Задайте SNI, клиентский ключ, short ID и UUID соответствующего устройства. Проверьте прохождение HTTPS через туннель из той сети, в которой устройство обычно используется.

## Повторный запуск и откат

После изменения параметров снова выполните `uv run --locked --no-dev vpn-manager container deploy`. После проверки конфигурации Xray-контейнер пересоздаётся, подключение кратковременно прервётся.

Операции с контейнерами выполняются на текущем хосте. Для VPS сначала войдите
по SSH и откройте каталог проекта. Для текущего AdminVPS с 3x-ui эти операции
подойдут только после перехода на Docker Compose.

~~~bash
uv run vpn-manager container validate
uv run vpn-manager container test --settings .env.deploy
uv run vpn-manager container status xray
uv run vpn-manager container logs xray
uv run vpn-manager container up xray
uv run vpn-manager container stop xray
uv run vpn-manager container start xray
uv run vpn-manager container restart xray
uv run vpn-manager container kill xray
~~~

Если Docker в системе требует повышенных прав, после `uv sync` вызывайте
`sudo .venv/bin/vpn-manager container ...`. Право управления Docker нужно
доступу администратора, фоновому сервису проверок оно не требуется.

Диагностика:

```bash
sudo docker compose --env-file /dev/null ps
sudo docker compose --env-file /dev/null logs --tail 100 xray
```

Если требуется вернуть предыдущую конфигурацию, восстановите и исходные значения в `.env.deploy`, чтобы следующий запуск не повторил проблемное изменение:

```bash
sudo test -f .local/xray.previous.json
sudo cp -p .local/xray.previous.json .local/xray.json
sudo docker compose --env-file /dev/null up -d --force-recreate
```

Схема развёртывания использует [образ Xray 26.7.28](https://github.com/xtls/Xray-core/pkgs/container/xray-core/1073572420) и [настройки REALITY](https://xtls.github.io/en/config/transports/reality.html).
