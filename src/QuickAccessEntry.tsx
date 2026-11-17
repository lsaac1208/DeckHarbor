import { Navigation } from "@decky/ui";
import { ActionButton, UIStyles, PageHeader } from "./ui";
import { MY_LIBRARY_ROUTE, LIBRARY_ROUTE, EMU_MANAGER_ROUTE, SETTINGS_ROUTE } from "./routes";
export default function QuickAccessEntry() {
  return <div className="nfd-ui" style={{ padding: 24 }}><UIStyles /><PageHeader title="DeckHarbor" subtitle="我的游戏与模拟器" /><div className="nfd-actions">
    <ActionButton onClick={() => Navigation.Navigate(MY_LIBRARY_ROUTE)}>我的游戏</ActionButton>
    <ActionButton onClick={() => Navigation.Navigate(LIBRARY_ROUTE)}>发现游戏</ActionButton>
    <ActionButton onClick={() => Navigation.Navigate(EMU_MANAGER_ROUTE)}>模拟器中心</ActionButton>
    <ActionButton onClick={() => Navigation.Navigate(SETTINGS_ROUTE)}>设置</ActionButton>
  </div></div>;
}
