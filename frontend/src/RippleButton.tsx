import { gsap } from 'gsap';
import { useEffect, useRef, type AnchorHTMLAttributes, type ButtonHTMLAttributes, type MouseEvent } from 'react';
import { useMotion } from './motion';

type Props = ({ href: string } & AnchorHTMLAttributes<HTMLAnchorElement>) |
  ({ href?: never } & ButtonHTMLAttributes<HTMLButtonElement>);

export function RippleButton(props: Props) {
  const { enabled, navigate } = useMotion();
  const ink = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const element = ink.current;
    return () => {
      gsap.killTweensOf(element);
      if (element) element.style.opacity = '0';
    };
  }, [enabled]);

  function ripple(event: MouseEvent<HTMLElement>) {
    const element = ink.current;
    if (!enabled || !element) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    element.style.left = (event.detail ? event.clientX - bounds.left : bounds.width / 2) + 'px';
    element.style.top = (event.detail ? event.clientY - bounds.top : bounds.height / 2) + 'px';
    gsap.fromTo(element, { scaleX: .35, scaleY: .5, x: 0, opacity: .24 }, {
      scaleX: 1 + bounds.width / 10, scaleY: 1 + bounds.height / 12, x: 8,
      opacity: 0, duration: .47, ease: 'power2.out', overwrite: true,
    });
  }

  const content = <>{props.children}<span className="ripple-ink" ref={ink} aria-hidden="true" /></>;
  const className = ['ripple', props.className].filter(Boolean).join(' ');
  if (props.href !== undefined) {
    return <a {...props} href={props.href} className={className} onClick={(event) => {
      props.onClick?.(event);
      if (!event.defaultPrevented) ripple(event);
      if (props.href.startsWith('#')) navigate(event);
    }}>{content}</a>;
  }
  return <button {...props} className={className} type={props.type ?? 'button'} onClick={(event) => {
    props.onClick?.(event);
    if (!event.defaultPrevented) ripple(event);
  }}>{content}</button>;
}
