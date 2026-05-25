import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"IBM Plex Sans"', "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
      },
      colors: {
        bg: "#0c0c0e",
        surface: "#131316",
        "surface-2": "#17171a",
        line: "#1f1f24",
        muted: "#6b6b73",
        dim: "#8a8a92",
        fg: "#ebebef",
        accent: "#c8b886",
        signal: "#7ab8b0",
        warn: "#d9b070",
        err: "#d97062",
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
    },
  },
} satisfies Config;
