"use client";

// Загрузка api-maps один раз на страницу.
declare global {
  interface Window { ymaps?: any; }
}

let ymapsPromise: Promise<any> | null = null;

export function loadYmaps(): Promise<any> {
  if (!ymapsPromise) {
    ymapsPromise = new Promise((resolve, reject) => {
      const w = window as any;
      if (w.ymaps) { w.ymaps.ready(() => resolve(w.ymaps)); return; }
      const s = document.createElement("script");
      s.src = "https://api-maps.yandex.ru/2.1/?lang=ru_RU" +
        (process.env.NEXT_PUBLIC_YMAPS_KEY
          ? `&apikey=${process.env.NEXT_PUBLIC_YMAPS_KEY}` +
            `&suggest_apikey=${process.env.NEXT_PUBLIC_YMAPS_KEY}` : "");
      s.async = true;
      s.onload = () => w.ymaps.ready(() => resolve(w.ymaps));
      s.onerror = () => { ymapsPromise = null; reject(new Error("не удалось загрузить Яндекс.Карты")); };
      document.head.appendChild(s);
    });
  }
  return ymapsPromise;
}
