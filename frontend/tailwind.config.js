/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        dark: {
          bg: '#080b11',
          panel: '#0f141f',
          card: '#141b2a',
          border: '#1f293d',
          hover: '#1b2438',
        },
        laser: {
          green: '#00e676',
          cyan: '#00f2fe',
        },
        crimson: {
          red: '#ff1744',
        },
        amber: {
          warn: '#ffab00',
        },
        purple: {
          smc: '#a855f7',
        }
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'Liberation Mono', 'Courier New', 'monospace'],
      }
    },
  },
  plugins: [],
}
