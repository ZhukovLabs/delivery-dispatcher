import type { Metadata, Viewport } from "next";
import "./globals.css";
import Providers from "@/components/Providers";

export const metadata: Metadata = {
  title: "Диспетчер доставки",
  description: "Распределение заказов и маршруты курьеров",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

const themeInit = `try{if(localStorage.getItem("theme")==="dark")document.documentElement.classList.add("dark")}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
      </head>
      <body><Providers>{children}</Providers></body>
    </html>
  );
}
