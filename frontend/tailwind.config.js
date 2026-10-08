/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#1d1d1f',
        sub: '#6e6e73',
        faint: '#aeaeb2',
        accent: '#0071e3',
        'accent-dark': '#0060c9',
        bg: '#f5f5f7',
        line: '#e5e5ea',
        ok: '#34c759',
        warn: '#ff9500',
        bad: '#ff3b30',
        island: '#000000',
      },
      borderRadius: {
        card: '20px',
        'card-sm': '16px',
        pill: '999px',
      },
      boxShadow: {
        card: '0 1px 2px rgba(0,0,0,0.04), 0 8px 24px rgba(0,0,0,0.06)',
        'card-lg': '0 2px 4px rgba(0,0,0,0.05), 0 16px 40px rgba(0,0,0,0.10)',
        island: '0 4px 16px rgba(0,0,0,0.35)',
      },
      fontFamily: {
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          '"SF Pro Text"',
          '"PingFang SC"',
          '"Hiragino Sans GB"',
          '"Microsoft YaHei"',
          'sans-serif',
        ],
        mono: ['"Paper Mono"', 'ui-monospace', '"SF Mono"', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
