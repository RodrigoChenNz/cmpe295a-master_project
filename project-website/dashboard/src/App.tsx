import { useEffect, useState } from "react";
import Sidebar from "./components/layout/Sidebar";
import DashboardPage from "./pages/DashboardPage";
import ProductsPage from "./pages/ProductsPage";
import { products as mockProducts } from "./data/mockData";

// think of the End User isntead of focusing on manager --> RBAC (target end users that allows them view the store ingventory, and alsoo see the competitor prices, and 
// also provide them capability to make reservation/purchase online (anywhere as long conencted to the Internet)
// Maybe do an APP for mobile? this will be much more accessible to the end users (faciliate whatever you can for the end users)
// Add location? depending where the user is and find the optimal path (travel time and cost in mind)
// Add niche products (category for local products)

export default function App() {
  const [products, setProducts] = useState<any[]>([]);
  const [selectedProduct, setSelectedProduct] = useState<any>(null);

  const [searchTerm, setSearchTerm] = useState("");
  const [riskFilter, setRiskFilter] = useState("All");

  const [activePage, setActivePage] = useState("Dashboard");
  const [dataSource, setDataSource] = useState<"Live API" | "Mock Data">(
    "Mock Data"
  );

  async function fetchProducts() {
    try {
      const res = await fetch("http://localhost:8000/products");

      if (!res.ok) {
        throw new Error("Backend response failed");
      }

      const data = await res.json();

      setProducts(data);
      setDataSource("Live API");

      setSelectedProduct((current: any) => {
        if (!current) return data[0];
        return data.find((p: any) => p.id === current.id) || data[0];
      });
    } catch (error) {
      console.warn("Backend unavailable. Using mock data.");

      setProducts(mockProducts);
      setDataSource("Mock Data");

      setSelectedProduct((current: any) => {
        if (!current) return mockProducts[0];
        return (
          mockProducts.find((p: any) => p.id === current.id) ||
          mockProducts[0]
        );
      });
    }
  }

  useEffect(() => {
    fetchProducts();

    const interval = setInterval(fetchProducts, 5000);
    return () => clearInterval(interval);
  }, []);

  const filteredProducts = products.filter((product: any) => {
    const matchesSearch = product.name
      .toLowerCase()
      .includes(searchTerm.toLowerCase());

    const matchesRisk =
      riskFilter === "All" || product.risk === riskFilter;

    return matchesSearch && matchesRisk;
  });

  function handleSaveLimits(
    productId: number,
    minPrice: number,
    maxPrice: number
  ) {
    const updatedProducts = products.map((product) =>
      product.id === productId
        ? { ...product, minPrice, maxPrice }
        : product
    );

    setProducts(updatedProducts);

    const updatedSelected = updatedProducts.find(
      (p) => p.id === productId
    );

    if (updatedSelected) {
      setSelectedProduct(updatedSelected);
    }
  }

  if (!selectedProduct) {
    return <div className="p-6">Loading dashboard...</div>;
  }

  return (
    <div className="flex min-h-screen bg-gray-100">
      <Sidebar
        activePage={activePage}
        setActivePage={setActivePage}
        dataSource={dataSource}
      />

      <main className="flex-1 p-8 overflow-x-hidden">
        {activePage === "Dashboard" && (
          <DashboardPage
            products={products}
            filteredProducts={filteredProducts}
            selectedProduct={selectedProduct}
            setSelectedProduct={setSelectedProduct}
            searchTerm={searchTerm}
            setSearchTerm={setSearchTerm}
            riskFilter={riskFilter}
            setRiskFilter={setRiskFilter}
            handleSaveLimits={handleSaveLimits}
          />
        )}

        {activePage === "Products" && (
          <ProductsPage products={products} />
        )}
      </main>
    </div>
  );
}