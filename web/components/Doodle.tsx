const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

/** One of our own doodles (scripts/make_doodles.py). Decorative unless `alt` is given. */
export default function Doodle({
  name,
  size = 40,
  alt = "",
  className,
}: {
  name: string;
  size?: number;
  alt?: string;
  className?: string;
}) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={`${BASE}/doodles/${name}.svg`} width={size} height={size} alt={alt} className={className} draggable={false} />
  );
}
