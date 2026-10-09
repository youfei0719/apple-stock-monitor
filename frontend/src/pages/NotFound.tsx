import { Link } from 'react-router-dom';
import { PageHeader } from '../components/ui';

/** 404：未知路由不再静默回首页，明确告知 + 回首页入口 */
export default function NotFound() {
  return (
    <div>
      <PageHeader title="页面不存在" subtitle="地址可能输错了，或者页面已经搬走" />
      <div className="px-4 pb-6 text-center">
        <Link
          to="/"
          className="inline-block px-8 py-3 rounded-pill bg-accent text-white text-sm font-medium active:scale-95 transition"
        >
          回首页
        </Link>
      </div>
    </div>
  );
}
