import { useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
// Add description for the PriceHisotry 
type Range = "7D" | "30D" | "1Y";

export default function PriceHistoryChart({ product }: any) {
  const [range, setRange] = useState<Range>("30D");

  const data =
    range === "7D"
      ? product.history7d
      : range === "30D"
      ? product.history30d
      : product.history1y;

  return (
    <div className="bg-white p-6 rounded-2xl shadow min-h-[430px]">
      <div className="flex justify-between items-start mb-6">
        <div>
          <h2 className="text-2xl font-bold">Price History</h2>
          <p className="text-gray-500">{product.name}</p>
        </div>

        <div className="flex gap-2">
          {["7D", "30D", "1Y"].map((item) => (
            <button
              key={item}
              onClick={() => setRange(item as Range)}
              className={`px-3 py-1 rounded-lg text-sm ${
                range === item
                  ? "bg-blue-600 text-white"
                  : "bg-gray-100 text-gray-600"
              }`}
            >
              {item}
            </button>
          ))}
        </div>
      </div>

      <div className="h-[320px]">
        <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data}>
            <CartesianGrid strokeDasharray="4 4" vertical={false} />
            <XAxis dataKey="date" />
            <YAxis tickFormatter={(value) => `$${value}`} />
            <Tooltip
                formatter={(value: number, name: string) => [
                `$${value.toFixed(2)}`,
                name === "current" ? "Current" : "AI Suggested",
                ]}
            />

            <Line
                type="monotone"
                dataKey="current"
                stroke="#2563eb"
                strokeWidth={3}
                dot={false}
            />

            <Line
                type="monotone"
                dataKey="ai"
                stroke="#059669"
                strokeWidth={3}
                strokeDasharray="6 6"
                dot={false}
            />
            </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

