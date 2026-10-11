import { ArrowRight, Bookmark, Brain, CalendarDays, Check, ChevronRight, CircleHelp, Clock3, Copy, Download, House, Leaf, Menu, MessageCircle, Moon, MoreHorizontal, NotebookPen, Package, Plus, Settings2, ShieldCheck, ShoppingBasket, Sparkles, Sun, Target, ThumbsDown, ThumbsUp, Utensils, Wrench, X } from "lucide-react";

const icons = { calendar: CalendarDays, plus: Plus, basket: ShoppingBasket, bookmark: Bookmark, dish: Utensils,
  chat: MessageCircle, product: Package, target: Target, settings: Settings2, download: Download,
  more: MoreHorizontal, home: House, leaf: Leaf, arrow: ArrowRight, chevron: ChevronRight,
  help: CircleHelp, shield: ShieldCheck, sparkles: Sparkles, sun: Sun, moon: Moon, check: Check,
  like: ThumbsUp, dislike: ThumbsDown, clock: Clock3, menu: Menu, close: X, notebook: NotebookPen,
  brain: Brain, wrench: Wrench, copy: Copy };

export default function Icon({ name }: { name: string }) {
  const Component = icons[name as keyof typeof icons] ?? MoreHorizontal;
  return <Component size={19} strokeWidth={1.7} aria-hidden="true" className="icon" />;
}
