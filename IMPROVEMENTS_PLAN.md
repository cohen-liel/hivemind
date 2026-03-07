# 📋 תוכנית שיפורים מקיפה — Telegram Claude Bot

## סקירה כללית

תוכנית זו מפרטת את כל השיפורים שמיועדים להפוך את הבוט לכלי שניתן להשתמש בו **מרחוק** —
בלי לשבת מול המחשב. המשתמש יוכל לתזמן משימות, לקבל התראות חכמות, לראות דשבורד מרוכז,
ולחזור אחרי היעדרות לסיכום מלא של מה שקרה.

---

## 🔔 1. מערכת התראות חכמה (Smart Notifications)

### מה נוסף:
- **פקודת `/notify`** — הגדרת העדפות התראות (הכל / סיכומים בלבד / שקט)
- **אזהרת תקציב ב-80%** — הבוט מתריע כשמתקרבים לגבול התקציב
- **התראות סיום משופרות** — סיכום מובנה עם קבצים שהשתנו, שורות קוד, פקודות שרצו
- **התראת תקיעות (Stall Alert)** — הודעה יזומה אם הסוכן תקוע יותר מ-60 שניות

### קבצים שהשתנו:
- `bot.py` — פקודת `/notify`, לוגיקת התראות
- `orchestrator.py` — אזהרת תקציב, זיהוי תקיעות משופר
- `session_manager.py` — טבלת `notification_prefs`

### פרטי מימוש:
```
notification_prefs טבלה:
  - user_id (PK)
  - level: 'all' | 'summary' | 'quiet'
  - budget_warning: boolean (default true)
  - stall_alert: boolean (default true)
```

---

## 📊 2. דשבורד משופר (Enhanced Dashboard)

### מה נוסף:
- **פקודת `/dashboard`** — תצוגת כל הפרויקטים במבט אחד עם כפתורים אינליין
- תצוגה: שם פרויקט, סטטוס (🟢/🔴/🟡), עלות כוללת, זמן פעילות אחרון, מספר סוכנים
- **פקודת `/history`** — 10 המשימות האחרונות שהסתיימו עם סטטוס הצלחה/כישלון

### קבצים שהשתנו:
- `bot.py` — פקודות `/dashboard` ו-`/history`
- `session_manager.py` — טבלת `task_history`, שאילתות מצטברות

### פרטי מימוש:
```
task_history טבלה:
  - id (PK, AUTOINCREMENT)
  - project_id
  - user_id
  - task_description
  - status: 'success' | 'failed' | 'timeout' | 'cancelled'
  - cost_usd
  - turns_used
  - started_at
  - completed_at
  - summary (תיאור קצר של מה נעשה)
```

---

## 📝 3. סיכום תוצאות חכם (Smart Result Summary)

### מה נוסף:
- אחרי סיום משימה, הפלט של הסוכן מנותח אוטומטית
- מיצוי: קבצים שנוצרו/שונו/נמחקו, פקודות שרצו, טסטים שעברו/נכשלו
- סיכום מובנה עם אימוג'ים כהודעה הסופית במקום פלט גולמי

### קבצים שהשתנו:
- `orchestrator.py` — מחלקת `ResultSummaryParser`, פונקציית `_build_smart_summary()`

### דוגמת פלט:
```
✅ המשימה הושלמה בהצלחה!

📁 קבצים שהשתנו:
  ✨ נוצר: src/auth/login.py
  ✏️ עודכן: src/app.py (+15, -3)
  🗑️ נמחק: src/old_auth.py

💻 פקודות שרצו:
  • pip install flask-login
  • python -m pytest tests/

🧪 טסטים:
  ✅ 12 עברו | ❌ 0 נכשלו

💰 עלות: $0.0342 | ⏱️ זמן: 45 שניות | 🔄 סיבובים: 3
```

---

## 🌙 4. מצב היעדרות (Away Mode)

### מה נוסף:
- **פקודת `/away`** — מעבר למצב דייג'סט (עדכונים מרוכזים, סיכומים בלבד)
- **פקודת `/catchup`** — דוח מפורט של כל מה שקרה בזמן ההיעדרות
- שמירת סטטוס היעדרות ב-SQLite

### קבצים שהשתנו:
- `bot.py` — פקודות `/away` ו-`/catchup`
- `session_manager.py` — עמודת `away_mode` בטבלת projects, טבלת `away_digest`

### פרטי מימוש:
```
away_digest טבלה:
  - id (PK)
  - user_id
  - project_id
  - event_type: 'task_complete' | 'error' | 'budget_warning' | 'stall'
  - summary
  - timestamp

projects טבלה — עמודה חדשה:
  - away_mode INTEGER DEFAULT 0
```

---

## 🔄 5. צינור אוטומטי (Auto Pipeline)

### מה נוסף:
- **פקודת `/pipeline`** — הגדרת רצף משימות
- דוגמה: `/pipeline develop: add login page | review | test`
- הבוט מריץ כל שלב ברצף ומדווח תוצאה משולבת
- מחלקת `PipelineManager` שמשרשרת ביצועי סוכנים

### קבצים שהשתנו:
- `orchestrator.py` — מחלקת `PipelineManager`
- `bot.py` — פקודת `/pipeline`

### פרטי מימוש:
```python
class PipelineManager:
    """מנהל צינור משימות עם שלבים רציפים."""

    async def run_pipeline(self, steps: list[PipelineStep]):
        """מריץ כל שלב ברצף, מעביר תוצאות בין שלבים."""
        for step in steps:
            result = await self._execute_step(step, previous_result)
            if result.is_error:
                break  # עצירה בשגיאה
            previous_result = result
```

---

## ⏰ 6. משימות מתוזמנות (Scheduled Tasks)

### מה נוסף:
- קובץ חדש `scheduler.py` עם אינטגרציה ל-asyncio scheduler
- **פקודת `/schedule`** — `/schedule 08:00 run tests on project X`
- **פקודת `/schedules`** — רשימת כל המשימות המתוזמנות
- שמירה ב-SQLite (טבלת `schedules`)

### קבצים חדשים:
- `scheduler.py` — מנהל תזמון מבוסס asyncio

### פרטי מימוש:
```
schedules טבלה:
  - id (PK)
  - user_id
  - chat_id
  - project_id
  - schedule_time TEXT (HH:MM)
  - task_description
  - repeat: 'once' | 'daily' | 'weekdays'
  - enabled INTEGER DEFAULT 1
  - last_run REAL
  - created_at REAL
```

---

## סדר עדיפויות מימוש

| # | פיצ'ר | עדיפות | מורכבות | השפעה |
|---|--------|---------|---------|-------|
| 1 | התראות חכמות | 🔴 קריטי | בינונית | גבוהה |
| 2 | דשבורד | 🔴 קריטי | נמוכה | גבוהה |
| 3 | סיכום תוצאות | 🟡 גבוה | בינונית | גבוהה |
| 4 | מצב היעדרות | 🟡 גבוה | בינונית | גבוהה |
| 5 | צינור אוטומטי | 🟢 רגיל | גבוהה | בינונית |
| 6 | משימות מתוזמנות | 🟢 רגיל | גבוהה | בינונית |

---

## דרישות טכניות

- **Python 3.11+**
- **python-telegram-bot >= 21.0** (async)
- **aiosqlite** — עבור כל הפרסיסטנס
- **asyncio** — בסיס התזמון (ללא תלויות חיצוניות)
- כל הקוד חייב להיות תואם async
- מחרוזות למשתמש בעברית (כמו בקוד הקיים)
- שמירה על הדפוסים והארכיטקטורה הקיימים

---

## סטטוס מימוש

- [x] תוכנית שיפורים (מסמך זה)
- [x] מערכת התראות חכמה
- [x] דשבורד משופר
- [x] סיכום תוצאות חכם
- [x] מצב היעדרות
- [x] צינור אוטומטי
- [x] משימות מתוזמנות
