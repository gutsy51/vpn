# Консоль управления VPN

Запускайте консоль из корня репозитория в Windows cmd. Ниже также приведены примеры для WSL. Для облачных API скопируйте `.env.example` в свой локальный `.env`, оставьте ключи нужных провайдеров и установите права `0600`. Для секретов предпочтительна Linux-файловая система WSL.

## Интерактивное меню

~~~bash
uv run vpn-manager
~~~

Главное меню открывается в интерактивном терминале. В WSL используйте стрелки, Enter и Esc, либо цифры от `1` до `9` и `0` для возврата. Для списка VPS доступны `n` и `p` для страниц, `r` для обновления и `/` для поиска по ID или названию на текущей странице. В терминалах без поддержки стрелок консоль показывает нумерованный список. Действие над питанием требует повторно ввести показанный ID. Перед отправкой тикета Fornex показывает точный текст для подтверждения.

Раздел мониторинга читает сохранённое состояние и очередь доставки, запускает разовую проверку без уведомлений и позволяет отправить тест после подтверждения. Для системного наблюдателя используйте `vpn-manager ui --state /var/lib/vpn-manager/state.sqlite3 --config /etc/vpn-manager/monitor.toml` из учётной записи, у которой есть доступ к этим файлам. При необходимости можно указать другие пути в самом меню. Запуск фонового сервиса описаны в [документации мониторинга](README.md).

Консоль подхватывает `.env` из текущего каталога. Чтобы взять ключи из другого файла, укажите его явно:

~~~bash
uv run vpn-manager --env-file ~/.config/vpn-manager/accounts.env ui
~~~

Создайте окружение проекта и запустите консоль через uv:

~~~bash
uv sync --locked --link-mode copy
uv run vpn-manager
~~~

Команды ниже остаются доступны для скриптов. При необходимости укажите отдельный
файл настроек через `--env-file`. По умолчанию консоль берёт переменные из `.env` в текущем каталоге.

## AdminVPS

Подтверждён кабинет с заказами и тикет-системой. Проверяемого публичного API для этих действий я не нашёл. Добавьте алиас `adminvps` в `%USERPROFILE%\.ssh\config` в Windows или `~/.ssh/config` в WSL, проверьте отпечаток хоста по консоли провайдера и настройте SSH-ключ. Для заказов и отправки обращения используется кабинет с обычной авторизацией и 2FA.

~~~sshconfig
Host adminvps
    HostName vpn-node.example.com
    User admin
    IdentityFile ~/.ssh/adminvps_ed25519
    IdentitiesOnly yes
~~~

Подставьте действующий адрес и пользователя, которому уже выдали доступ по ключу. Примите ключ хоста после сверки отпечатка. CLI использует строгую проверку ключа хоста и пакетный режим без запроса пароля. Если алиас нужен и для старого адреса, задайте `ADMINVPS_SSH_HOST` и `ADMINVPS_SSH_KNOWN_HOSTS` в локальном `.env`. Для обходного адреса используйте отдельный файл с проверенным публичным ключом SSH-хоста. Консоль сохраняет настройки аутентификации из алиаса.

~~~bash
uv run vpn-manager adminvps portal
uv run vpn-manager adminvps ssh-status
uv run vpn-manager adminvps ticket-draft --order 12345 \
  --subject 'Уточнение по основному IPv4' --body-file .local/ticket.txt
~~~

`ticket-draft` формирует текст для кабинета. [AdminVPS о поддержке](https://adminvps.ru/)

## Fornex

Получите `FORNEX_API_KEY` в кабинете Fornex. Консоль обращается к `ru.fornex.com` напрямую, чтобы не пересылать заголовок авторизации при редиректе с основного домена. Просмотр заказа, статус и запрос на перезапуск VPS, черновик обращения:

~~~bash
uv run vpn-manager fornex orders
uv run vpn-manager fornex order 34-1234
uv run vpn-manager fornex power-status 34-1234
uv run vpn-manager fornex power 34-1234 reboot
uv run vpn-manager fornex ticket --order 34-1234 \
  --subject 'Замена IPv4 на VPS' --body-file .local/ticket.txt
~~~

Для выполнения действия добавьте `--apply` к `power` или `--send` к `ticket`. После тайм-аута сверяйтесь с кабинетом перед повторной отправкой. [Fornex Public API](https://ru.fornex.com/api/)

## OneDash

Получите `ONEDASH_API_KEY` в кабинете. Если ключ ограничен по IP, добавьте внешний адрес машины, с которой запускается WSL. Для питания нужен ID VPS, для `order` - ID заказа.

~~~bash
uv run vpn-manager onedash balance
uv run vpn-manager onedash orders
uv run vpn-manager onedash order 10001
uv run vpn-manager onedash vps
uv run vpn-manager onedash ip-options 20001
uv run vpn-manager onedash power-status 20001
uv run vpn-manager onedash power 20001 reboot
~~~

`ip-options` показывает варианты и цену, включая признак необходимости пересоздания VPS. Покупка, изменение IP и переустановка остаются ручными операциями через кабинет. `power` показывает команду до отправки, а с `--apply` ставит её в очередь провайдера. Для проверки завершения используйте `power-status`. [OneDash API](https://github.com/OneDashRDP/api-docs/blob/main/Documentation%202.0%20EN.md)
