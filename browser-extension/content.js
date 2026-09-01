/* Scraper-Fallback: liest die Aufgabenliste direkt aus dem DOM aus.
   Wird nur aktiv, wenn die Seite /homework geoeffnet ist. */

function scrapeTasks() {
  const tasks = [];
  document.querySelectorAll("a[href*='/homework/']").forEach((link) => {
    const id = link.getAttribute('href').replace(/\/+$/, '').split('/').pop();
    if (!/^[0-9a-f]{6,}$/.test(id) || tasks.some((t) => t.id === id)) return;

    const card = link.closest('[class*=card], li, tr') || link.parentElement;
    const text = card ? card.innerText.replace(/\s+/g, ' ').trim() : link.innerText;
    const due = text.match(/\d{1,2}\.\d{1,2}\.\d{2,4}(?:,?\s*\d{1,2}[:.]\d{2})?/);
    const course = card?.querySelector('[class*=course], .subtitle, small');

    tasks.push({
      id,
      title: link.innerText.trim() || 'Aufgabe',
      course: course ? course.innerText.trim() : '',
      due: due ? due[0] : '',
      status: /abgegeben|eingereicht|bewertet/i.exec(text)?.[0] || '',
      description: text.slice(0, 300),
    });
  });
  return tasks;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === 'scrape') sendResponse({ tasks: scrapeTasks() });
  return true;
});
