/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        pixel: ['"Press Start 2P"', '"Courier New"', 'monospace'],
        mono: ['"Courier New"', 'monospace'],
      },
      keyframes: {
        // 白点滴溜溜转：偏心放置，容器旋转一圈即画出圆形巡视轨迹
        eyeroll: {
          '0%': { transform: 'rotate(0deg)' },
          '100%': { transform: 'rotate(360deg)' },
        },
      },
      animation: {
        eyeroll: 'eyeroll 2.6s linear infinite',
      },
    },
  },
  plugins: [],
};
