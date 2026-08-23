import type { SVGProps } from 'react';

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function base({ size = 18, ...props }: IconProps) {
  return {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.6,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    ...props,
  };
}

export function SunriseIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M4 18h16" />
      <path d="M6.5 14.5a5.5 5.5 0 0 1 11 0" />
      <path d="M12 5v3" />
      <path d="M5.6 8.6l1.8 1.8" />
      <path d="M18.4 8.6l-1.8 1.8" />
    </svg>
  );
}

export function BooksIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M5 4h3.5v16H5z" />
      <path d="M8.5 6H12v14H8.5" />
      <path d="M14.2 5.2l3.4.9-3.4 13.7-3.4-.9z" />
    </svg>
  );
}

export function CycleIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M20 12a8 8 0 1 1-2.3-5.6" />
      <path d="M20 3v4h-4" />
    </svg>
  );
}

export function CompassIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="12" cy="12" r="9" />
      <path d="M15.5 8.5l-2 5-5 2 2-5z" />
    </svg>
  );
}

export function TrendIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M3 17l6-6 4 4 8-8" />
      <path d="M21 12V7h-5" />
    </svg>
  );
}

export function SearchIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="M20 20l-4.2-4.2" />
    </svg>
  );
}

export function PlusIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </svg>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M4.5 12.5l5 5 10-11" />
    </svg>
  );
}

export function ArrowRightIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M4 12h16" />
      <path d="M13 5l7 7-7 7" />
    </svg>
  );
}

export function DocumentIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M7 3h7l4 4v14H7z" />
      <path d="M14 3v4h4" />
      <path d="M10 12h5M10 16h5" />
    </svg>
  );
}

export function SparkIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />
      <path d="M18.5 15.5l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z" />
    </svg>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M6 6l12 12" />
      <path d="M18 6L6 18" />
    </svg>
  );
}

export function GearIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M18.5 5.5l-1.7 1.7M7.2 16.8l-1.7 1.7" />
    </svg>
  );
}

export function ListChecksIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M4 6.5l1.4 1.4L8 5.3" />
      <path d="M4 12.5l1.4 1.4L8 11.3" />
      <path d="M4 18.5l1.4 1.4L8 17.3" />
      <path d="M11 6.5h9M11 12.5h9M11 18.5h9" />
    </svg>
  );
}

export function PlugIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M9 3.5v4M15 3.5v4" />
      <path d="M7 7.5h10v3.5a5 5 0 0 1-5 5 5 5 0 0 1-5-5z" />
      <path d="M12 16v4.5" />
    </svg>
  );
}

export function WrenchIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M14.5 6.5a4 4 0 0 0-5.3 5.3L3.8 17.2a2 2 0 1 0 2.8 2.8l5.4-5.4a4 4 0 0 0 5.3-5.3l-2.6 2.6-2.5-.7-.7-2.5z" />
    </svg>
  );
}

export function StarIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M12 3.5l2.5 5.2 5.5.8-4 3.9.9 5.6-4.9-2.7-4.9 2.7.9-5.6-4-3.9 5.5-.8z" />
    </svg>
  );
}

export function PdfIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M7 3h7l4 4v14H7z" />
      <path d="M14 3v4h4" />
      <path d="M9.5 13h1.2a1.3 1.3 0 0 1 0 2.6H9.5zM9.5 15.6V18" />
      <path d="M13.5 13v5" />
    </svg>
  );
}

export function NoteIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M6 3h9l4 4v14H6z" />
      <path d="M15 3v4h4" />
      <path d="M9 12h6M9 15.5h4" />
    </svg>
  );
}
