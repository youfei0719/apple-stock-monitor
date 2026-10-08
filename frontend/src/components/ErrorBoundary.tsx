import { Component, type ReactNode } from 'react';
import { Card } from './ui';

/**
 * R6-D6：全站 ErrorBoundary——渲染崩溃（脏数据/组件异常）时兜底显示出错页，
 * 与 ErrorState 组件风格统一：圆角 Card + 感叹号图标 + 再试一次按钮。
 * 重试走整页重新加载，避免 boundary 被错误状态锁死。
 */
export default class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary]', error, info);
  }

  private retry = () => {
    this.setState({ error: null });
    window.location.reload();
  };

  render() {
    const { error } = this.state;
    if (error) {
      return (
        <div className="min-h-screen bg-bg text-ink font-sans flex items-center justify-center">
          <div className="w-full max-w-lg px-4">
            <Card className="p-8 text-center rise-in">
              <div className="mx-auto mb-4 w-14 h-14 rounded-full bg-bg flex items-center justify-center">
                <span className="text-2xl text-bad">!</span>
              </div>
              <p className="text-ink font-medium">页面出了点问题</p>
              <p className="mt-2 text-sm text-sub break-all">{error.message || '未知错误'}</p>
              <button
                onClick={this.retry}
                className="mt-5 px-6 py-2.5 rounded-pill bg-accent text-white text-sm font-medium active:scale-95 transition"
              >
                重新加载
              </button>
            </Card>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
