/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Dark navy chrome from the reference design.
        navy: { DEFAULT: '#0f2440', 700: '#16305425', 800: '#122a4b', 900: '#0b1c33' },
        flood: {
          // Legend bins, used by both the map and the charts so they never drift.
          deep: '#8b1a1a',    // > 10 m
          high: '#e8762c',    // 5 - 10 m
          mid: '#f2d024',     // 2 - 5 m
          low: '#7fc4e8',     // 0.5 - 2 m
          trace: '#2b7bba',   // 0.1 - 0.5 m
        },
      },
    },
  },
  plugins: [],
}
