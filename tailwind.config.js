/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: "class",
  content: [
    "./app/static/**/*.{html,js}",
    "./app/templates/**/*.html",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', 'Inter', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace']
      },
      colors: {
        "surface-container-highest": "#d4e4fc", "on-tertiary-container": "#868382",
        "outline-variant": "#c4c7c7", "inverse-on-surface": "#eaf1ff",
        "surface-container": "#e5eeff", "secondary-fixed-dim": "#ffb3ad",
        "on-background": "#1a1a1a", "secondary": "#b51822",
        "on-tertiary-fixed-variant": "#484645", "error-container": "#ffdad6",
        "on-primary-fixed-variant": "#474746", "surface-variant": "#d4e4fc",
        "on-surface-variant": "#444748", "outline": "#747878",
        "tertiary-fixed-dim": "#cac6c4", "surface-container-lowest": "#ffffff",
        "on-primary": "#ffffff", "primary-fixed-dim": "#c8c6c5",
        "secondary-container": "#d93537", "on-secondary": "#ffffff",
        "on-surface": "#1a1a1a", "on-secondary-container": "#fffbff",
        "background": "#fbfbfb", "primary-container": "#1c1b1b",
        "on-error": "#ffffff", "tertiary": "#000000", "tertiary-fixed": "#e6e2df",
        "inverse-primary": "#c8c6c5", "on-primary-fixed": "#1c1b1b",
        "on-tertiary": "#ffffff", "tertiary-container": "#1c1b1a", "surface": "#fbfbfb",
        "on-secondary-fixed": "#410004", "on-secondary-fixed-variant": "#930013",
        "on-tertiary-fixed": "#1c1b1a", "on-primary-container": "#858383",
        "surface-container-high": "#dce9ff", "error": "#ba1a1a",
        "surface-container-low": "#eff4ff", "surface-dim": "#ccdbf4",
        "surface-bright": "#fbfbfb", "inverse-surface": "#223144",
        "primary": "#1a1a1a", "secondary-fixed": "#ffdad7",
        "surface-tint": "#5f5e5e", "on-error-container": "#93000a", "primary-fixed": "#e5e2e1"
      },
      borderRadius: { "DEFAULT": "0.125rem", "lg": "0.25rem", "xl": "0.5rem", "full": "0.75rem" },
      spacing: { "xs": "4px", "xl": "32px", "md": "16px", "gutter": "12px", "margin-x": "16px", "sm": "8px", "lg": "24px" }
    }
  },
  plugins: []
};
