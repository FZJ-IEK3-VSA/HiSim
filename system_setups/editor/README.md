# HiSim Scenario Editor

A browser-based visual editor for `*.scenario.json` files. Components appear as cards in a node-graph canvas; ports are connected by bezier curves. Built with Vite + React + TypeScript + React Flow + Tailwind CSS + Zustand.

---

## Prerequisites

| Tool | Version | How to install |
|------|---------|----------------|
| Node.js | **20 LTS** | via [nvm-windows](https://github.com/coreybutler/nvm-windows/releases) — download and run `nvm-setup.exe`, then: `nvm install 20 && nvm use 20` |
| Python | 3.10+ in **hisimvenv** conda env | already set up with the main HiSim install |

Verify Node is available:
```powershell
node --version   # v20.x.x
npm --version    # 10.x.x
```

---

## One-time setup

From the repo root, activate your Python environment and install the HiSim package if you haven't:
```bash
pip install -e .
```

Then install the editor's JavaScript dependencies:
```powershell
cd system_setups/editor
npm install
```

---

## Generating the component database

The editor reads two static JSON files that describe all HiSim components and their ports. Re-generate them whenever you add or modify components:

```bash
# from the repo root, with hisimvenv active
python tools/generate_component_db.py
```

Output files (committed to the repo so the editor works without running the script):
- `system_setups/editor/public/data/component_db.json` — 71 components with inputs, outputs, config fields, and default connections
- `system_setups/editor/public/data/enum_db.json` — all LoadTypes, Units, ComponentType, etc. enums

The script exits non-zero if any component fails introspection; failures are listed in `component_db.json` under `"failures"`.

---

## Development

```powershell
cd system_setups/editor
npm run dev
```

Opens at `http://localhost:5173`. Hot-reload is on — save any `.tsx`/`.ts`/`.css` file and the browser updates instantly.

---

## Production build

```powershell
cd system_setups/editor
npm run build       # output goes to system_setups/editor/dist/
npm run preview     # serve the built output locally for a final check
```

---

## Project layout

```
system_setups/editor/
├── public/
│   └── data/
│       ├── component_db.json   # auto-generated — do not edit by hand
│       └── enum_db.json        # auto-generated — do not edit by hand
├── src/
│   ├── main.tsx                # entry point
│   ├── App.tsx                 # three-panel shell (Palette · Canvas · Inspector)
│   ├── index.css               # Tailwind base imports
│   ├── components/
│   │   ├── Toolbar.tsx         # top action bar
│   │   ├── Palette.tsx         # left sidebar — component list
│   │   ├── Canvas.tsx          # centre — React Flow graph
│   │   ├── Inspector.tsx       # right sidebar — config form
│   │   └── StatusBar.tsx       # bottom — validation messages
│   └── store/
│       └── index.ts            # Zustand store (nodes, edges, selection, validation)
├── SPEC.md                     # full UI specification and implementation plan
├── index.html
├── package.json
├── vite.config.ts
├── tsconfig.json
├── tailwind.config.js
└── postcss.config.js
```

---

## Dependency versions (pinned in package.json)

| Package | Version | Purpose |
|---------|---------|---------|
| `@xyflow/react` | ^12 | node-graph canvas |
| `react` / `react-dom` | ^18 | UI framework |
| `zustand` | ^5 | global state |
| `tailwindcss` | ^3 | utility CSS |
| `vite` | ^5 | build tool |
| `typescript` | ^5 | type checking |
