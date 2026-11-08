# 🧭 AxiomTrace — Desktop Application

**AxiomTrace** — десктопное приложение для визуального анализа, парсинга и выполнения кода.
Стек: **Electron** (оболочка) + **Next.js** (фронтенд) + **Python/Flask** (бэкенд).

---

## 👤 Для обычных пользователей

Готовые сборки доступны в разделе **[GitHub Releases](https://github.com/<owner>/AxiomTrace/releases)**.
Скачайте файл, подходящий для вашей операционной системы, и запустите его — установка не требуется (кроме Windows-установщика).

### 🪟 Windows
- **Установщик:** `AxiomTrace-Setup-<version>.exe` — стандартная установка с ярлыком в меню «Пуск».
- **Портативная версия:** `AxiomTrace-<version>-portable.exe` — запускается без установки, удобно для флешки/сетевой папки.

### 🍎 macOS
- **Образ диска:** `AxiomTrace-<version>.dmg` — откройте файл и перетащите `AxiomTrace.app` в папку `Applications`.

> ⚠️ При первом запуске macOS может показать предупреждение безопасности (приложение не из App Store). Разрешите запуск через **Системные настройки → Конфиденциальность и безопасность → «Всё равно открыть»**.

### 🐧 Linux
- **AppImage:** `AxiomTrace-<version>.AppImage` — сделайте файл исполняемым и запустите:
  ```bash
  chmod +x AxiomTrace-<version>.AppImage
  ./AxiomTrace-<version>.AppImage
  ```
- **Debian/Ubuntu:** `AxiomTrace-<version>.deb` — установка через пакетный менеджер:
  ```bash
  sudo dpkg -i AxiomTrace-<version>.deb
  # при необходимости:
  sudo apt-get install -f
  ```

### ✅ Что входит в сборку
- Фронтенд (Next.js, собрано статически).
- Встроенный бэкенд (Flask) — запускается автоматически при старте приложения, отдельно ничего поднимать не нужно.
- Изолированный Python-интерпретатор и зависимости — всё уже внутри пакета.

> 💡 Если приложение не стартует, проверьте, что порт `5000` свободен (бэкенд использует его по умолчанию) и что антивирус не блокирует исполняемый файл.

---

## 🛠 Для разработчиков

### Требования

- **Node.js** 18+ и **npm**
- **Python** 3.10+ (рекомендуется 3.12)
- **Git**

### 1. Клонирование репозитория

```bash
git clone https://github.com/<owner>/AxiomTrace.git
cd AxiomTrace
```

### 2. Установка зависимостей бэкенда (Python/Flask)

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
cd ..
```

### 3. Установка зависимостей фронтенда (Next.js + Electron)

```bash
cd frontend
npm install
cd ..
```

### 4. Запуск в режиме разработки

Один терминал — всё сразу (Next.js dev-сервер + Electron + авто-старт backend):

```bash
cd frontend
npm run electron:dev
```

Команда `electron:dev` через `concurrently` параллельно поднимает:
1. `npm run dev` — Next.js dev-сервер на `http://localhost:3000`.
2. `electron . --no-renderer-spawn` — окно Electron, которое подключается к dev-серверу.

Electron сам запускает бэкенд (`backend/app.py`) на `http://127.0.0.1:5000`
и ждёт ответа от `/api/v1/health`.

### 5. Запуск production-сборки локально

```bash
cd frontend
npm run build          # собирает Next.js в .next
npm run electron       # запускает Electron (он сам поднимет backend)
```

### 6. Запуск компонентов по отдельности (для отладки)

**Только бэкенд:**
```bash
cd backend
.venv/bin/python app.py
# → http://127.0.0.1:5000
```

**Только фронтенд:**
```bash
cd frontend
npm run dev
# → http://localhost:3000
```

### 7. Проверка качества (тесты, линтер, сборка)

```bash
# бэкенд
cd backend && .venv/bin/python -m pytest -q

# фронтенд
cd frontend && npm test && npx eslint src && npm run build
```

### ⚠️ Важные замечания

- Бэкенд по умолчанию слушает порт `5000`. Убедитесь, что он свободен.
- Electron ищет интерпретатор в `backend/.venv/bin/python` (Linux/macOS) или
  `backend/.venv/Scripts/python.exe` (Windows). Без venv backend не стартует.
- При проблемах с чёрным окном Electron (Mesa/NVIDIA/Wayland) включите
  программный рендеринг (по умолчанию уже включён) или принудительно:
  ```bash
  AXIOM_ENABLE_GPU=1 npm run electron:dev
  ```
- Если фронтенд не обновляется — очистите кэш сборки:
  ```bash
  cd frontend && npm run clean && npm run build
  ```

---

## 📄 Лицензия

См. файл [LICENSE](./LICENSE).