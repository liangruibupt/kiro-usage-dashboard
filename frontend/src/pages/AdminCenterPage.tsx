import type { ReactNode } from "react";
import { Layout, Menu } from "antd";
import { TeamOutlined, PercentageOutlined } from "@ant-design/icons";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { AdminUsersPanel } from "./AdminManagementPage";
import { MultiplierMaintenancePage } from "./MultiplierMaintenancePage";
import { useI18n } from "../i18n";

const { Sider, Content } = Layout;

/**
 * 管理中心菜单配置（数组驱动）。
 * 扩展：新增一项管理功能 = 加一条配置 + 一个子面板组件。
 */
interface AdminMenuItem {
  key: string;
  labelKey: string;
  path: string;
  icon: ReactNode;
  element: ReactNode;
}

const MENU_ITEMS: AdminMenuItem[] = [
  {
    key: "users",
    labelKey: "adminCenter.users",
    path: "/admin/users",
    icon: <TeamOutlined />,
    element: <AdminUsersPanel />,
  },
  {
    key: "multipliers",
    labelKey: "adminCenter.multipliers",
    path: "/admin/multipliers",
    icon: <PercentageOutlined />,
    element: <MultiplierMaintenancePage />,
  },
];

/**
 * 管理中心：AppShell（active='admin'）包裹 Ant Layout.Sider + Menu。
 * 按当前子路由渲染对应面板；点击菜单 navigate 到子路由。
 */
export function AdminCenterPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();

  const current = MENU_ITEMS.find((m) => location.pathname === m.path);

  // /admin 或未知子路径 → 默认跳到第一项（管理员管理）。
  if (!current) {
    return <Navigate to={MENU_ITEMS[0].path} replace />;
  }

  return (
    <AppShell active="admin">
      <Layout style={{ background: "transparent" }}>
        <Sider width={200} theme="light" style={{ background: "transparent" }}>
          <Menu
            mode="inline"
            selectedKeys={[current.key]}
            onClick={({ key }) => {
              const item = MENU_ITEMS.find((m) => m.key === key);
              if (item) navigate(item.path);
            }}
            items={MENU_ITEMS.map((m) => ({ key: m.key, icon: m.icon, label: t(m.labelKey) }))}
            style={{ borderInlineEnd: "none" }}
          />
        </Sider>
        <Content style={{ paddingInlineStart: 20, minWidth: 0 }}>{current.element}</Content>
      </Layout>
    </AppShell>
  );
}
