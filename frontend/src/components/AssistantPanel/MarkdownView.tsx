import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MarkdownViewProps {
  content: string;
}

/**
 * 助手消息的 Markdown 渲染：
 * 支持标题/粗体/列表/表格/代码块/引用/链接等（remark-gfm 扩展表格与删除线）。
 */
function MarkdownView({ content }: MarkdownViewProps) {
  return (
    <div className="md-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, ...props }) => (
            <a target="_blank" rel="noreferrer" {...props}>
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export default memo(MarkdownView);