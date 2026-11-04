CREATE TABLE supplier_items (
 supplier_id UUID NOT NULL REFERENCES suppliers,
 product_id TEXT NOT NULL REFERENCES products,
 title TEXT NOT NULL, unit_cost NUMERIC(10,2) NOT NULL CHECK(unit_cost>=0),
 available INT NOT NULL DEFAULT 10000 CHECK(available>=0), PRIMARY KEY(supplier_id,product_id)
);
ALTER TABLE ledger_entries ADD COLUMN account TEXT NOT NULL DEFAULT 'merchandise'
 CHECK(account IN ('merchandise','tax_payable','shipping_income','inventory_adjustment'));
