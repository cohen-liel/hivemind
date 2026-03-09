# מדריך Anthropic לניסוח Prompts לסוכני AI: יישום בפרויקט web-claude-bot

נכתב על ידי **Manus AI** עבור פרויקט web-claude-bot.

מסמך זה מרכז את התובנות המרכזיות מהפרסומים הרשמיים של Anthropic בנושא בניית סוכני AI אפקטיביים [1] [2] [3] [4], ומנתח כיצד ניתן ליישם עקרונות אלו על ה-System Prompts הקיימים בפרויקט `web-claude-bot`.

## עקרונות המפתח של Anthropic לסוכנים (Context Engineering)

חברת Anthropic, המפתחת של מודלי Claude, מדגישה כי מעבר ל"הנדסת פרומפטים" (Prompt Engineering) מסורתית, בעולם הסוכנים יש להתמקד ב**הנדסת הקשר** (Context Engineering). סוכנים הפועלים בלולאות ארוכות צוברים מידע רב, ויש לנהל את "תקציב תשומת הלב" (Attention Budget) שלהם בקפידה כדי למנוע "ריקבון הקשר" (Context Rot) [2].

### 1. בהירות וגובה הטיסה (The "Right Altitude")
Anthropic ממליצה למצוא את נקודת האיזון המדויקת בהנחיות המערכת: לא ספציפי מדי (המוביל לקוד שביר ונוקשה) ולא כללי מדי (המוביל לחוסר הבנה של המטרה). יש לספק לסוכן כללים מנחים חזקים שיאפשרו לו לקבל החלטות גמישות, אך להגדיר בבירור את ציפיות הפלט [2].

### 2. שימוש בתגיות XML
מודלי Claude מאומנים להבין היטב תגיות XML. Anthropic ממליצה בחום להשתמש בתגיות XML כדי להפריד בין סוגי מידע שונים בפרומפט (למשל `<instructions>`, `<context>`, `<example>`). הדבר עוזר למודל להבין את ההיררכיה של המידע ולמנוע בלבול [4].

### 3. הדרכה מה לעשות (במקום מה לא לעשות)
גישה חיובית מניבה תוצאות טובות יותר. במקום לכתוב "אל תשתמש ב-Markdown", עדיף לכתוב "כתוב את התשובה בפסקאות טקסט רציפות". הדבר נכון גם לגבי התנהגות סוכנים: במקום "אל תשאיר משימות חצי גמורות", עדיף "השלם כל משימה עד הסוף לפני המעבר למשימה הבאה" [4].

### 4. דוגמאות (Few-Shot Prompting)
מתן 3-5 דוגמאות מובנות היטב (בתוך תגיות `<example>`) הוא הדרך האמינה ביותר לכוון את הפורמט, הטון והמבנה של פלט הסוכן. הדוגמאות צריכות להיות מגוונות ולשקף מקרי קצה [4].

## ניתוח המצב הקיים בפרויקט web-claude-bot

לאחר בחינת קובץ ה-`config.py` של הפרויקט, ניכר כי ה-System Prompts הקיימים כבר מיישמים חלק ניכר מההמלצות, אך יש מקום משמעותי לשיפור.

| סוכן | חוזקות נוכחיות | חולשות לשיפור (לפי Anthropic) |
|---|---|---|
| **Orchestrator** | חלוקה ברורה לשלבים, הגדרת תפקיד חזקה, שימוש בדוגמאות ל-`<delegate>` | עמוס מאוד בטקסט חופשי, חסר שימוש נרחב בתגיות XML להפרדת מידע |
| **Developer** | רשימות תיוג (Checklists) ברורות, דרישה לעדכון ה-Manifest | חסרות דוגמאות פלט ספציפיות, שימוש ב-bullet points במקום תגיות XML מובנות |
| **Reviewer / Tester** | התמקדות בתוצאות אמיתיות (Actual Output), רשימות בדיקה מובנות | פורמט הדיווח נאכף באמצעות טקסט חופשי במקום מבנה סמנטי חזק |
| **Specialists (Typed Contract)** | פורמט פלט מוגדר היטב ב-JSON, הגדרת מומחיות ברורה | הבלוק הכללי של `_TYPED_CONTRACT_FOOTER` ארוך ועלול לגרום ל"ריקבון הקשר" |

## המלצות מעשיות לשדרוג הסוכנים בפרויקט

כדי להפוך את הסוכנים בפרויקט ל"חכמים" יותר ויציבים יותר, מומלץ לבצע את השינויים הבאים ב-`config.py`:

### א. ארגון מחדש של הפרומפטים עם תגיות XML
יש לעטוף את החלקים השונים של הפרומפטים בתגיות XML ברורות. לדוגמה, עבור ה-Orchestrator:

```markdown
<role>
You are the Orchestrator — the strategic brain of a multi-agent software engineering team.
You are a THINKER, INSPECTOR, and COORDINATOR.
</role>

<instructions>
1. READ MANIFEST: Check .nexus/PROJECT_MANIFEST.md
2. UNDERSTAND: What exactly is being asked?
...
</instructions>

<delegation_format>
Use <delegate> blocks with JSON. Each block = one agent with one focused task.
</delegation_format>
```

### ב. הוספת דוגמאות מובנות (Few-Shot)
כדי להבטיח שהסוכנים מפיקים את ה-JSON והפורמטים הרצויים, יש להוסיף בלוקים של `<examples>`. הדבר קריטי במיוחד עבור ה-Typed Contract Protocol החדש.

```markdown
<examples>
<example>
<input>
Task: Implement rate limiting middleware in FastAPI.
</input>
<output>
```json
{
  "task_id": "task_123",
  "status": "completed",
  "summary": "Added per-IP rate limiting middleware using slowapi.",
  ...
}
```
</output>
</example>
</examples>
```

### ג. ניהול "ריקבון הקשר" (Context Rot)
ה-`ORCHESTRATOR_SYSTEM_PROMPT` הנוכחי ארוך מאוד (כ-200 שורות). Anthropic מזהירה כי מודלים מאבדים מיקוד כאשר ההקשר ארוך מדי [2]. 
מומלץ:
1. לצמצם חזרות.
2. להעביר חלק מההנחיות (כמו רשימת הסוכנים הזמינים) להיות מוזרקות דינמית רק כשיש בהן צורך.
3. להשתמש בהנחיות חיוביות ("Do X") במקום רשימות ארוכות של איסורים ("NEVER do Y") [4].

### ד. שיפור כלי הסוכנים (Tools)
על פי המדריך "Writing effective tools for AI agents" [3], יש לוודא שהכלים (Tools) שהסוכנים מקבלים מוגדרים היטב. בפרויקט זה, הכלים מנוהלים כנראה ב-`skills_registry.py` וב-`contracts.py`. יש לוודא שלכל כלי יש תיאור מדויק הכולל את סוגי הפרמטרים, מתי להשתמש בו, ומה הפלט הצפוי.

## סיכום

הארכיטקטורה של `web-claude-bot`, ובמיוחד המעבר ל-Typed Contract Protocol מבוסס DAG, מתיישבת היטב עם תבניות העבודה המתקדמות (Workflows) ש-Anthropic מתארת (Orchestrator-workers ו-Evaluator-optimizer) [1]. 

השדרוג המרכזי הנדרש כעת הוא ברמת הניסוח המיקרוסקופי: מעבר מטקסט חופשי למבנה XML ממושמע, הוספת דוגמאות קונקרטיות, וצמצום אורך הפרומפטים כדי לשמור על מיקוד הסוכנים לאורך סשנים ארוכים.

---
## References

[1] Anthropic. "Building Effective Agents". https://www.anthropic.com/engineering/building-effective-agents (Dec 19, 2024).
[2] Anthropic. "Effective Context Engineering for AI Agents". https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents (Sep 29, 2025).
[3] Anthropic. "Writing Effective Tools for AI Agents". https://www.anthropic.com/engineering/writing-tools-for-agents (Sep 11, 2025).
[4] Anthropic. "Prompting Best Practices". Claude API Docs. https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices.
