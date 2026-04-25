export function Disclaimer({ className = "" }: { className?: string }) {
  return (
    <p
      className={
        "text-[10px] uppercase tracking-[0.18em] text-fii-mute " + className
      }
    >
      For educational purposes only. Not investment advice.
    </p>
  );
}
