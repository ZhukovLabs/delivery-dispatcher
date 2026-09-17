export type Row = {
  closed_at: string;
  address: string;
  courier: string;
  outcome: string;
  cycle_min: number | null;
  deadline: string;
  payment?: string;
  pay_amount?: number | null;
};
export type Route = { courier_name: string; status: string; stops: string[] };
export type CourierStat = { courier: string; km: number; taken: number; delivered: number; cancelled: number; revenue?: number; pay_cash: number; pay_card: number; work_min: number | null };
export type Data = {
  rows: Row[];
  summary: { delivered: number; cancelled: number; avg_cycle_min: number | null;
    pay_cash?: number; pay_cash_sum?: number; pay_card?: number; pay_card_sum?: number };
  routes: Route[];
  courier_stats?: CourierStat[];
  today: string;
  now: string;
};
