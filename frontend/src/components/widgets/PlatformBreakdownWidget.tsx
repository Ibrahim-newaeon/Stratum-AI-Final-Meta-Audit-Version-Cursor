import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import { cn, getPlatformColor } from '@/lib/utils';

interface PlatformBreakdownWidgetProps {
  className?: string;
}

const mockData = [
  { name: 'Facebook', value: 35, color: getPlatformColor('facebook') },
  { name: 'Instagram', value: 30, color: getPlatformColor('instagram') },
  { name: 'WhatsApp', value: 20, color: getPlatformColor('whatsapp') },
  { name: 'Messenger', value: 15, color: '#8B5CF6' },
];

export function PlatformBreakdownWidget({ className }: PlatformBreakdownWidgetProps) {
  return (
    <div className={cn('h-full p-4', className)}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={mockData}
            cx="50%"
            cy="50%"
            innerRadius={50}
            outerRadius={80}
            paddingAngle={2}
            dataKey="value"
          >
            {mockData.map((entry, index) => (
              <Cell key={`cell-${index}`} fill={entry.color} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              backgroundColor: 'hsl(var(--card))',
              border: '1px solid hsl(var(--border))',
              borderRadius: '8px',
            }}
            formatter={((value: number) => [`${value}%`, 'Share']) as any}
          />
          <Legend
            verticalAlign="bottom"
            height={36}
            formatter={(value) => <span className="text-xs text-foreground">{value}</span>}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}
