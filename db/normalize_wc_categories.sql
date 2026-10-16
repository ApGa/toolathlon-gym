-- Product JSON category IDs in the source fixture predate the category catalogue.
-- Resolve by name, preserving the catalogue IDs, product membership, and JSON order.
UPDATE wc.products AS p
SET categories = (
    SELECT jsonb_agg(
        CASE WHEN c.id IS NULL THEN item.category
             ELSE item.category || jsonb_build_object('id', c.id) END
        ORDER BY item.position
    )
    FROM jsonb_array_elements(p.categories) WITH ORDINALITY AS item(category, position)
    LEFT JOIN wc.product_categories AS c
        ON lower(c.name) = lower(item.category->>'name')
)
WHERE jsonb_typeof(p.categories) = 'array'
  AND EXISTS (
      SELECT 1
      FROM jsonb_array_elements(p.categories) AS item(category)
      JOIN wc.product_categories AS c ON lower(c.name) = lower(item.category->>'name')
      WHERE item.category->>'id' IS DISTINCT FROM c.id::text
  );
