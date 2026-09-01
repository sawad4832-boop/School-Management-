/** Optionaler lokaler Tailwind-Build (siehe README, Abschnitt "Ohne CDN").
 *  Bauen mit:  npx tailwindcss -i static/css/input.css -o static/css/tailwind.css --minify
 */
module.exports = {
  content: ['./templates/**/*.html', './static/js/**/*.js'],
  theme: {
    extend: {
      colors: { ink: '#0f172a', surface: '#f8fafc' },
    },
  },
  plugins: [],
};
