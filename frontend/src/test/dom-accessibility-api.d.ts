// The package's "exports" map has no "types" entry, so bundler resolution misses its typings.
declare module "dom-accessibility-api" {
  export function computeAccessibleName(element: Element): string;
}
