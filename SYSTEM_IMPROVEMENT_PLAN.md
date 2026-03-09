# תוכנית אסטרטגית לשיפור המערכת (web-claude-bot)

**מאת:** Manus AI
**תאריך:** מרץ 2026

מסמך זה מרכז את תוכנית הפעולה המלאה לשיפור המערכת בשלושה צירים מרכזיים: שיפור "המוח" של הסוכנים, תיקון ממשק המשתמש (UI), וחזון ארוך טווח.

---

## 1. שיפור "המוח" של הסוכנים (לפי מחקרי Anthropic)

הבסיס של המערכת מצוין (Orchestrator-Workers, DAG, Typed Contracts), אבל אנחנו מפספסים את הפוטנציאל המלא של Claude בגלל הדרך שבה אנחנו מתקשרים איתו.

### א. מעבר מלא ל-XML Tags (שיפור קריטי)
Claude אומן "לחשוב" בתוך תגיות XML [1]. כרגע אנחנו משתמשים בכותרות טקסט כמו `═══ YOUR ROLE ═══`.
**הפעולה:** שכתוב מלא של כל הפרומפטים ב-`config.py` לשימוש בתגיות:
* `<role>` - הגדרת התפקיד
* `<instructions>` - רשימת המטלות
* `<constraints>` - מה אסור לעשות
* `<context>` - מידע רקע

### ב. הוספת `<thinking>` לפני יצירת JSON
ב-`contracts.py`, הסוכנים מתבקשים להחזיר פלט JSON מיד בסוף העבודה. Anthropic מצאו שזה פוגע דרמטית באיכות [2].
**הפעולה:** הוספת הנחיה ב-`_TYPED_CONTRACT_FOOTER`:
```xml
Before generating the JSON, you MUST think step-by-step inside <thinking> tags.
Analyze the task, plan your artifacts, and verify you met all constraints.
Then, output the JSON block.
```

### ג. ניהול Skills דינמי (מניעת Context Rot)
ב-`skills_registry.py`, אנחנו מזריקים לפעמים יותר מדי Skills (עד 5, כל אחד יכול להיות ארוך). זה יוצר עומס קוגניטיבי (Context Rot) [3].
**הפעולה:** 
1. צמצום מספר ה-Skills המקסימלי ל-2-3 הקריטיים ביותר.
2. יצירת מנגנון שבו הסוכן יכול לבקש לקרוא Skill ספציפי רק אם הוא צריך אותו (כמו Tool), במקום להזריק הכל מראש.

---

## 2. תיקון ממשק המשתמש (UI) והתקשורת

### א. ביטול התקשורת הישירה עם סוכנים (Single Point of Entry)
המשתמש ציין שהוא יכול לדבר ישירות עם סוכנים וזה שובר את המודל של ה-Orchestrator.
**הפעולה:** ב-`frontend/src/components/Controls.tsx`:
* הסרת ה-Dropdown של בחירת הסוכן (`targetAgent`).
* כל ההודעות נשלחות ל-`orchestrator` כברירת מחדל. ה-Orchestrator הוא ה"מנצח" (Conductor) היחיד שמקבל הנחיות מהמשתמש ומחליט למי להעביר אותן.

### ב. תצוגת סטטוס אמיתית לסוכנים
ב-`frontend/src/components/AgentStatusPanel.tsx`, יש בעיה בסנכרון הסטטוסים במצב DAG.
**הפעולה:**
* עדכון ה-WebSocket handler כדי לוודא שאירועי `agent_started` ו-`agent_finished` מה-DAG Executor מעדכנים את ה-`agent_states` בזמן אמת.
* הוספת תצוגת "Sub-tasks" תחת ה-Orchestrator, כך שהמשתמש רואה בדיוק איזו משימה ה-Orchestrator חילק לאיזה סוכן.

---

## 3. חזון לטווח ארוך: סוכנים מומחים אמיתיים

כדי להפוך כל סוכן ל"הכי טוב בתחום שלו", המערכת צריכה לעבור מארכיטקטורה של "הנחיות כלליות" ל"כלים מותאמים אישית":

### א. כלים ייעודיים לכל תפקיד (Specialized Tools)
כרגע כל הסוכנים חולקים כלי Bash או קריאת קבצים כלליים.
* **Test Engineer:** צריך לקבל כלי `run_test_suite` שמחזיר פלט מובנה של כישלונות, לא רק טקסט חופשי.
* **Security Auditor:** צריך כלי להרצת סורקי אבטחה סטטיים (SAST) כמו Bandit או Semgrep.
* **Database Expert:** צריך כלי להרצת `EXPLAIN ANALYZE` ישירות על ה-DB המקומי.

### ב. מנגנון Self-Reflection & Evaluation
לפי תבנית "Evaluator-Optimizer" של Anthropic [4]:
* לפני ש-Developer מסיים משימה, הוא חייב להעביר את הקוד שלו לסוכן `Critic` פנימי.
* ה-`Critic` יריץ בדיקות אוטומטיות ויחזיר ל-Developer משוב. רק אם ה-Critic מאשר, המשימה חוזרת ל-Orchestrator.

### ג. Memory Agent פרואקטיבי
ה-Memory Agent צריך להיות לא רק "רשם", אלא "יועץ".
* לפני שה-Orchestrator מתכנן משימה, ה-Memory Agent שולף "Lessons Learned" מבאגים דומים בעבר ומזריק אותם לפרומפט של ה-Orchestrator.

---
### References
[1] Anthropic, "Claude Prompting Best Practices", Claude API Documentation.
[2] Anthropic, "Writing Effective Tools for AI Agents", Sep 2025.
[3] Anthropic, "Effective Context Engineering for AI Agents", Sep 2025.
[4] Anthropic, "Building Effective Agents", Dec 2024.
