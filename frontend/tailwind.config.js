/**
 * tailwind.config.js — M8M Design System
 *
 * ALL colors are CSS-variable-backed. No hardcoded hex values live here.
 * The full palette is declared in styles.css :root / .dark blocks.
 * Tailwind utilities like `text-surface-500`, `bg-accent`, etc. resolve
 * through these variables at runtime, making theme switching instant.
 *
 * Color variable convention:
 *   --c-<role>-<step>   e.g. --c-surface-700, --c-accent, --c-success
 */

/** @type {import('tailwindcss').Config} */
module.exports = {
    content: ['./src/**/*.{html,ts}'],
    darkMode: 'class',
    theme: {
        extend: {
            fontFamily: {
                sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
                mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
            },

            colors: {
                // ── Neutral surface scale ────────────────────────────────────
                // Sourced from --c-surface-* in :root / .dark
                surface: {
                    50:  'rgb(var(--c-surface-50)  / <alpha-value>)',
                    100: 'rgb(var(--c-surface-100) / <alpha-value>)',
                    200: 'rgb(var(--c-surface-200) / <alpha-value>)',
                    300: 'rgb(var(--c-surface-300) / <alpha-value>)',
                    400: 'rgb(var(--c-surface-400) / <alpha-value>)',
                    500: 'rgb(var(--c-surface-500) / <alpha-value>)',
                    600: 'rgb(var(--c-surface-600) / <alpha-value>)',
                    700: 'rgb(var(--c-surface-700) / <alpha-value>)',
                    800: 'rgb(var(--c-surface-800) / <alpha-value>)',
                    900: 'rgb(var(--c-surface-900) / <alpha-value>)',
                    950: 'rgb(var(--c-surface-950) / <alpha-value>)',
                },

                // ── Accent (Indigo-Violet) ───────────────────────────────────
                accent: {
                    DEFAULT: 'rgb(var(--c-accent)       / <alpha-value>)',
                    light:   'rgb(var(--c-accent-light)  / <alpha-value>)',
                    dark:    'rgb(var(--c-accent-dark)   / <alpha-value>)',
                    subtle:  'rgb(var(--c-accent)        / 0.1)',
                    glow:    'rgb(var(--c-accent)        / 0.25)',
                },

                // ── Semantic status ──────────────────────────────────────────
                success: {
                    DEFAULT: 'rgb(var(--c-success)      / <alpha-value>)',
                    glow:    'rgb(var(--c-success)       / 0.25)',
                },
                warning: {
                    DEFAULT: 'rgb(var(--c-warning)      / <alpha-value>)',
                    glow:    'rgb(var(--c-warning)       / 0.25)',
                },
                danger: {
                    DEFAULT: 'rgb(var(--c-danger)       / <alpha-value>)',
                    glow:    'rgb(var(--c-danger)        / 0.25)',
                },

                // ── Canvas background ────────────────────────────────────────
                canvas: {
                    DEFAULT: 'rgb(var(--c-canvas)       / <alpha-value>)',
                },

                // ── Node category accents (fixed — not theme-dependent) ──────
                node: {
                    http:     '#3b82f6',
                    schedule: '#f59e0b',
                    transform:'#8b5cf6',
                },
            },

            boxShadow: {
                // Glow shadows consume CSS var so they adapt to the palette
                'glow-accent':   '0 0 24px rgb(var(--c-accent)   / 0.18)',
                'glow-success':  '0 0 24px rgb(var(--c-success)  / 0.18)',
                'glow-danger':   '0 0 24px rgb(var(--c-danger)   / 0.18)',
                'node':          'var(--shadow-node)',
                'node-hover':    'var(--shadow-node-hover)',
                'panel':         'var(--shadow-panel)',
            },

            borderColor: {
                DEFAULT: 'rgb(var(--c-border) / <alpha-value>)',
            },

            backdropBlur: { xs: '2px' },

            animation: {
                'pulse-slow':      'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
                'slide-in-right':  'slideInRight 0.3s ease-out',
                'slide-in-left':   'slideInLeft 0.25s ease-out',
                'fade-in':         'fadeIn 0.2s ease-out',
                'dropdown-in':     'dropdownIn 0.15s ease-out',
            },

            keyframes: {
                slideInRight: {
                    '0%':   { transform: 'translateX(100%)', opacity: '0' },
                    '100%': { transform: 'translateX(0)',    opacity: '1' },
                },
                slideInLeft: {
                    '0%':   { transform: 'translateX(-100%)', opacity: '0' },
                    '100%': { transform: 'translateX(0)',     opacity: '1' },
                },
                fadeIn: {
                    '0%':   { opacity: '0', transform: 'translateY(-4px)' },
                    '100%': { opacity: '1', transform: 'translateY(0)'    },
                },
                dropdownIn: {
                    '0%':   { opacity: '0', transform: 'translateY(-4px)' },
                    '100%': { opacity: '1', transform: 'translateY(0)'    },
                },
            },
        },
    },
    plugins: [],
};
