import { useRef } from "react";

/* Закрытие по клику на фон: закрываем только если и нажатие, и отпускание
   кнопки произошли на самом фоне. Зажатие внутри модалки с выводом
   курсора наружу (например, при выделении текста) окно не закрывает. */
export function useBackdropClose(onClose: () => void) {
  const down = useRef(false);
  return {
    onMouseDown: (e: React.MouseEvent) => { down.current = e.target === e.currentTarget; },
    onMouseUp: (e: React.MouseEvent) => {
      if (down.current && e.target === e.currentTarget) onClose();
      down.current = false;
    },
  };
}
