import "./LoadingOverlay.css";

type Props = {
  /** 是否显示（全屏遮罩，期间会拦截所有鼠标/键盘操作） */
  show: boolean;
  /** 展示的加载文案 */
  message: string;
};

/**
 * 全屏阻塞式加载提示层。
 * 用于：导入文档 / 写入知识库 / 生成图谱 等耗时操作，操作未完成前遮住整个界面，
 * 使用户无法进行其他任何操作，同时给出明确的加载反馈。
 */
export default function LoadingOverlay({ show, message }: Props) {
  if (!show) return null;
  return (
    <div className="loading-overlay" role="alert" aria-busy="true">
      <div className="loading-box">
        <span className="loading-spinner" />
        <span className="loading-text">{message}</span>
      </div>
    </div>
  );
}