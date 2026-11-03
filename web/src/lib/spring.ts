export function spring(from: number, to: number, velocity: number, { damping = 1, response = 0.35 }: { damping?: number; response?: number } = {}) {
  let value = from;
  let speed = velocity;
  const omega = 2 * Math.PI / response;
  return (dt: number) => {
    if (damping === 1) {
      const x = value - to;
      const c = speed + omega * x;
      const decay = Math.exp(-omega * dt);
      value = to + (x + c * dt) * decay;
      speed = (speed - omega * c * dt) * decay;
    } else {
      const steps = Math.max(1, Math.ceil(dt / 0.005));
      const h = dt / steps;
      for (let i = 0; i < steps; i++) {
        speed += (-omega * omega * (value - to) - 2 * damping * omega * speed) * h;
        value += speed * h;
      }
    }
    const done = Math.abs(value - to) < 0.001 && Math.abs(speed) < 0.001;
    return { value, velocity: speed, done };
  };
}
