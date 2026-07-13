# Protected File Analyzer

Protected File Analyzer הוא יישום ווב מבוסס Docker לניתוח מורשה של קבצים מוגנים בסיסמה. המערכת מקבלת קובץ מוגן, מחלצת ממנו hash מתאים לנסיון שחזור בעזרת כלי העזר של John the Ripper ממשפחת `*2john`, מפעילה מדיניות שחזור פנימית ומוגבלת, מפענחת את הקובץ אם השחזור מצליח, ואז מריצה ניתוח סטטי על הפלט המפוענח.

## מה הפרויקט פותר

בצוותים שמורשים לבדוק קבצים מוגנים יש בדרך כלל צורך בתהליך עקבי עבור:

- זיהוי פורמטים נתמכים
- חילוץ hash מתאים עם הכלי הנכון
- נסיון שחזור סיסמה תחת מגבלות זמן ומועמדים ברורות
- פענוח בטוח של הקובץ
- ניתוח סטטי של התוכן המפוענח

הפרויקט עוטף את התהליך הזה מאחורי ממשק ווב קטן ו API, תוך שמירת לוגיקת השחזור בתוך worker נפרד שאינו root.

## יכולות עיקריות

- ממשק דפדפן ו API מבוסס JSON
- הפרדה בין web service לבין worker
- שימוש מאומת ב John the Ripper וב extractors ממשפחת `*2john`
- מדיניות שחזור פנימית, מוגבלת וברורה
- הורדת artifact לאחר פענוח מוצלח
- ניתוח סטטי עם YARA, oletools, PDFiD ו ExifTool
- הסתרת סיסמאות מסטטוס, מדוחות ומפלטים נשמרים

## סוגי קבצים נתמכים

- ZIP: `.zip`
- 7-Zip: `.7z`
- PDF: `.pdf`
- Microsoft Office:
  - Word: `.doc`, `.docx`, `.docm`
  - Excel: `.xls`, `.xlsx`, `.xlsm`
  - PowerPoint: `.ppt`, `.pptx`, `.pptm`

## ארכיטקטורה כללית

- Web service: FastAPI עבור העלאה, סטטוס jobs, דוחות, artifacts, ביטול jobs והורדת raw output בצורה בטוחה.
- Worker נפרד: תהליך worker שסורק את מצב ה jobs בתיקיות משותפות ומבצע חילוץ, שחזור, פענוח וניתוח סטטי.
- John ו `*2john`: ה worker מאמת ומפעיל את John the Ripper ואת הכלים `zip2john`, `7z2john`, `pdf2john`, `office2john` לפי סוג הקובץ.
- ניתוח סטטי: לאחר פענוח מוצלח, ה worker מריץ את הסורקים שהוגדרו.
- תיקיות runtime משותפות: ה web וה worker חולקים תיקיות jobs ו wordlists דרך bind mounts.

## מבנה ריפו

```text
protected-file-analyzer/
├── .github/workflows/
├── docker/
│   └── Dockerfile
├── scripts/
│   ├── ensure-runtime-dirs.sh
│   └── pfactl.sh
├── src/
│   └── protected_file_analyzer/
├── tests/
│   └── fixtures/
├── wordlists/
├── .env.example
├── .gitignore
├── .dockerignore
├── compose.yaml
├── compose.build.yaml
├── install.sh
├── install.ps1
├── pyproject.toml
├── README.md
├── README.he.md
├── SECURITY.md
├── CONTRIBUTING.md
└── LICENSE
```

הערות:

- `runtime/` ו `data/` נוצרות בזמן ריצה ונמצאות ב `.gitignore`.
- קובץ ה Compose הראשי הוא `compose.yaml`.
- הגדרת ה build נמצאת ב `docker/Dockerfile`.

## דרישות מערכת

### מומלץ

- Docker Engine עם Docker Compose
- Linux, macOS או Windows שיכולים להריץ Docker
- מספיק מקום פנוי בדיסק עבור קבצים מועלים, artifacts ודוחות

### לפיתוח מקומי בלי Docker

- Python `3.11`
- John the Ripper והכלים המתאימים ממשפחת `*2john` על המארח
- תלויות סורקים על המארח אם רוצים התנהגות דומה לזו של הקונטיינרים

## התקנה מקומית

### אפשרות A: Docker Compose על Linux או macOS

פריסה רגילה משתמשת ב images בנויים מראש וממוספרים:

```bash
cd protected-file-analyzer
cp .env.example .env
./scripts/ensure-runtime-dirs.sh
docker compose pull
docker compose up -d
```

אפשר גם כך:

```bash
cd protected-file-analyzer
cp .env.example .env
./scripts/pfactl.sh start
```

פתיחת הממשק:

- `http://127.0.0.1:8088`

עצירה:

```bash
cd protected-file-analyzer
./scripts/pfactl.sh stop
```

### אפשרות B: Docker Compose על Windows

הגדרת מארח מומלצת:

- Windows 11 או Windows 10 עדכני
- Docker Desktop עם WSL 2 backend
- PowerShell
- checkout של הריפו על כונן מקומי ש Docker Desktop יכול לגשת אליו

מהשורש של הריפו ב PowerShell:

```powershell
cd protected-file-analyzer
Copy-Item .env.example .env
New-Item -ItemType Directory -Force -Path .\runtime\jobs | Out-Null
New-Item -ItemType Directory -Force -Path .\runtime\wordlists | Out-Null
docker compose pull
docker compose up -d
```

פתיחת הממשק:

- `http://127.0.0.1:8088`

עצירה:

```powershell
cd protected-file-analyzer
docker compose down
```

הערות עבור Windows:

- Docker Desktop יכול ליצור bind mount directories לבד, אבל עדיף להכין מראש את `runtime/jobs` ואת `runtime/wordlists`.
- אם שומרים שינויים מקומיים ב `.env`, יש להעתיק פעם אחת מ `.env.example` ואז לערוך רק את `.env` המקומי.

### אפשרות C: בדיקת build נקייה

ל CI, ולידציית release או build מקומי נקי:

```bash
cd protected-file-analyzer
cp .env.example .env
./scripts/ensure-runtime-dirs.sh
docker compose -f compose.yaml -f compose.build.yaml build --no-cache
docker compose -f compose.yaml -f compose.build.yaml up -d
```

אם מריצים Compose ישירות על עץ חדש בלי להכין את runtime directories, Docker עלול ליצור תיקיות עם owner או permissions שימנעו מה worker לכתוב. מומלץ להריץ קודם `./scripts/pfactl.sh start` או `./scripts/ensure-runtime-dirs.sh`.

## הרצה בלי Docker

המסלול הזה מיועד בעיקר לפיתוח מקומי. הוא פחות מבודד, ודורש שסביבת המארח תתאים לכלי ה worker.

דוגמה:

```bash
cd protected-file-analyzer
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'

# Terminal 1: web app
uvicorn protected_file_analyzer.app:app --host 127.0.0.1 --port 8088

# Terminal 2: worker loop
python -m protected_file_analyzer
```

## משתני סביבה

כל ההגדרות הנתמכות נמצאות ב `.env.example`.

### נדרש לשימוש מקומי רגיל

- `PFA_BIND_HOST`
- `PFA_BIND_PORT`
- `PFA_SECRET_KEY`

### הגדרות runtime נפוצות

- `PFA_APP_NAME`
- `PFA_DATA_ROOT`
- `PFA_STATIC_DIR`
- `PFA_WORDLISTS_DIR`
- `PFA_DEFAULT_ROCKYOU_PATH`
- `PFA_YARA_RULES_PATH`
- `PFA_TOOL_RUNNER_BACKEND`
- `PFA_RUNTIME_GID`

### הגדרות מתקדמות

- `PFA_WEB_IMAGE`
- `PFA_WORKER_IMAGE`
- `PFA_KALI_MCP_URL`
- `PFA_KALI_MCP_WORKER_PYTHON`
- `PFA_KALI_MCP_WORKER_PATH`
- `PFA_MAX_EXTRACTED_MB`
- `PFA_MAX_EXTRACTED_FILES`
- `PFA_CRACK_TIMEOUT_SECONDS`
- `PFA_MAX_CONCURRENT_CRACKS`
- `PFA_WORKER_POLL_INTERVAL_SECONDS`
- `PFA_CLEANUP_INTERVAL_SECONDS`
- `PFA_JOB_TTL_MINUTES`
- `PFA_WEB_CPU_LIMIT`
- `PFA_WEB_MEMORY_LIMIT`
- `PFA_WEB_PIDS_LIMIT`
- `PFA_WORKER_CPU_LIMIT`
- `PFA_WORKER_MEMORY_LIMIT`
- `PFA_WORKER_PIDS_LIMIT`

### דוגמת `.env`

```dotenv
PFA_BIND_HOST=127.0.0.1
PFA_BIND_PORT=8088
PFA_APP_NAME=Protected File Analyzer
PFA_TOOL_RUNNER_BACKEND=local
PFA_RUNTIME_GID=10001
PFA_SECRET_KEY=change-me
```

הערות:

- `.env` אינו מנוהל ב Git בכוונה.
- יש להשאיר את `PFA_SECRET_KEY` מקומי ולהחליף את ערך ברירת המחדל לפני שימוש אמיתי.

## בדיקת health

```bash
curl --fail http://127.0.0.1:8088/api/health
```

צריך לראות:

- `"ready": true`

## דוגמת זרימת API

שליחת job מורשה:

```bash
cd protected-file-analyzer

curl -X POST http://127.0.0.1:8088/api/jobs \
  -F 'authorization_confirmed=true' \
  -F 'protected_file=@./sample.pdf;type=application/pdf'
```

בדיקת סטטוס:

```bash
curl http://127.0.0.1:8088/api/jobs/<JOB_ID>
```

הורדת תוצרים:

```bash
curl http://127.0.0.1:8088/api/jobs/<JOB_ID>/report
curl -OJ http://127.0.0.1:8088/api/jobs/<JOB_ID>/artifact
curl -X DELETE http://127.0.0.1:8088/api/jobs/<JOB_ID>
```

## מקורות שחזור

זרימת המשתמש היא תמיד **Analyze file**. השירות בוחר את מדיניות השחזור הפנימית, ולא חושף למשתמש איזה מקור הצליח.

סדר מקורות השחזור המובנה הוא:

1. wordlists ארגוניים mounted תחת `./runtime/wordlists/`
2. סריקת PIN פנימית של 4 ספרות
3. סריקת דפוסים פנימית מבוססת Israeli ID
4. `rockyou.txt` מקומי, אם קיים

הערות:

- היישום עולה גם אם `rockyou.txt` לא זמין.
- `health` ו `capabilities` מדווחים אם `rockyou` קיים.
- ה UI לא חושף שמות פנימיים של אסטרטגיות שחזור.

## הרצת בדיקות

```bash
cd protected-file-analyzer
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

## הרצת lint

```bash
cd protected-file-analyzer
. .venv/bin/activate
ruff check .
```

## הרצת live end-to-end tests

כאשר ה stack כבר רץ מקומית:

```bash
cd protected-file-analyzer
. .venv/bin/activate
PFA_LIVE_BASE_URL=http://127.0.0.1:8088 python -m pytest -q tests/test_live_e2e.py
```

## מגבלות ידועות

- `rockyou.txt` לא נכלל כברירת מחדל בריפו או ב image.
- ברירת המחדל מיועדת לסביבות מקומיות או מבוקרות, לא לחשיפה לאינטרנט פתוח.
- המסלול בלי Docker תלוי בכלי המארח ופחות מבודד מהפריסה בקונטיינרים.
- backend מסוג `kali_mcp` דורש שירות תואם שמנוהל בנפרד.

## שיקולי אבטחה

- ה worker רץ כ `UID 10001`.
- בברירת המחדל ל worker אין פורטים חשופים, והוא משתמש ב `network_mode: none`.
- Compose מגדיר `cap_drop: ALL`, `no-new-privileges:true`, מערכת קבצים לקריאה בלבד, `tmpfs` עבור `/tmp` ומגבלות CPU, memory ו PID.
- כל הרצת John מקבלת `HOME` מבודד לכל job עם `.john` פרטי.
- תוצאות crack מוצלחות עוברות redaction לפני שמירה.
- JSON של סטטוס ודוח לא חושפים סיסמאות ששוחזרו.
- סיסמאות משוחזרות משמשות רק לזרימת הפענוח ונמחקות מקבצי secret, מקבצי pot, מ sessions ומ work directories זמניים בזמן cleanup.

ראו גם: `SECURITY.md`

## שימוש מורשה בלבד

יש להשתמש בפרויקט רק עבור קבצים, מערכות וסביבות שבבעלותכם או שיש לכם הרשאה מפורשת לנתח. אין להשתמש בו לשחזור סיסמאות לא מורשה, לנסיונות גישה לא מורשים, או לטיפול בחומר מוגן של צד שלישי ללא אישור.

## פתרון תקלות

### `ready` נשאר `false`

- בדקו `docker compose logs web worker`
- ודאו שה worker יכול לכתוב אל ה bind mounts של runtime
- הריצו `./scripts/ensure-runtime-dirs.sh` והעלו שוב

### ה worker נכשל עם permission error תחת `/data/jobs`

- בדרך כלל המשמעות היא ש Compose עלה על עץ חדש בלי הכנת runtime directories
- עצרו את ה stack, הריצו `./scripts/ensure-runtime-dirs.sh`, ואז העלו שוב

### `rockyou` לא זמין

- זה צפוי אם לא mount-תם או סיפקתם `rockyou.txt` תקין
- היישום עדיין עולה וממשיך עם שאר מקורות השחזור

### בדיקת Live E2E מדלגת

- הגדירו `PFA_LIVE_BASE_URL`, ואז הריצו שוב את `tests/test_live_e2e.py`

## רישיון

הריפו כולל כרגע קובץ `LICENSE` מסוג MIT.
