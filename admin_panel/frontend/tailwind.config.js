/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        base: '#0a0a0f',
        surface: '#111118',
        surface2: '#16161f',
        edge: '#1f1f2b',
        accent: {
          DEFAULT: '#22d3ee',
          green: '#34d399',
        },
        danger: '#f87171',
        warn: '#fbbf24',
      },
      keyframes: {
        'fade-in': {
          '0%': { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'pulse-live': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.4' },
        },
      },
      animation: {
        'fade-in': 'fade-in 0.25s ease-out',
        'pulse-live': 'pulse-live 1.6s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
