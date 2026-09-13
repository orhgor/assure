# Visual Checklist — Executive-Grade Standard

Use this during **Convergence (Days 22–24)** only. Do not fix visuals mid-sprint — log issues in [deferred.md](./deferred.md) and batch them here.

If it is **not** in this list, it does **not** block v1.0. If it **is**, you fix it. No debate.

---

## Executive-Grade Standard

### Typography

- One font family only (Inter). No mixed fonts.
- Heading sizes: h1=32px, h2=24px, h3=18px, body=15px. No exceptions.
- Line height: 1.5 for body, 1.25 for headings.

### Spacing

- All padding and margin use only: 4, 8, 12, 16, 24, 32, 48, 64 px.
- No element touches another without at least 8px gap.

### Color

- No hardcoded hex outside CSS variables.
- Only 3 accent colors in use: trust blue (`#3b82f6`), verified green (`#10b981`), warning amber (`#f59e0b`).
- Backgrounds: white for canvas, `#f8fafc` for panels, `#1e293b` for rails.

### Motion

- Every state change animates in ≤ 200ms.
- No bouncy or playful easing. Only ease-out or `cubic-bezier(0.4, 0, 0.2, 1)`.

### Copy

- Buttons: verbs (Assemble, Verify, Export).
- Errors: what happened + what to do next.
- Empty states: invite action, not describe absence.

---

## Convergence checklist (Days 22–24)

Walk every visible surface against the standard above. Mark each row ✅ or log a fix.

| Surface | Typography | Spacing | Color | Motion | Copy |
| --- | --- | --- | --- | --- | --- |
| State rail | | | | | |
| Left pane (runs stack) | | | | | |
| Main document canvas | | | | | |
| Staging area (diff columns) | | | | | |
| Operator prompt (⌘K) | | | | | |
| Evidence drawer | | | | | |
| Export / dossier flow | | | | | |

**Target:** 100% ✅ before Day 25 ICP demo.
