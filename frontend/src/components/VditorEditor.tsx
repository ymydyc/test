import { useEffect, useRef } from "react";
import Vditor from "vditor";
import "vditor/dist/index.css";

interface Props {
  /** 初始 Markdown 内容（仅首次挂载生效；切换文档时请用 key 重建组件） */
  initialValue: string;
  height?: string;
  onReady?: (vd: Vditor) => void;
}

/** Vditor Markdown 编辑器封装（React 生命周期托管）。 */
export default function VditorEditor({ initialValue, height = "100%", onReady }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const vditorRef = useRef<Vditor | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    let disposed = false;
    const vd = new Vditor(containerRef.current, {
      height,
      mode: "ir", // 即时渲染：所见即所得 + 源码
      value: initialValue,
      cache: { enable: false },
      toolbar: [
        "headings", "bold", "italic", "strike", "|",
        "list", "ordered-list", "check", "outdent", "indent", "|",
        "quote", "line", "code", "inline-code", "|",
        "table", "link", "|",
        "undo", "redo", "|", "fullscreen", "preview",
      ],
      after: () => {
        if (disposed) return;
        vditorRef.current = vd;
        onReady?.(vd);
      },
    });
    return () => {
      disposed = true;
      vditorRef.current = null;
      try {
        const anyVd = vd as unknown as { destroy?: () => void };
        anyVd.destroy?.();
      } catch {
        /* ignore */
      }
      if (containerRef.current) {
        containerRef.current.innerHTML = "";
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <div ref={containerRef} style={{ height }} />;
}

/** 供父组件获取编辑器实例（跨重建安全） */
export type { Vditor };
