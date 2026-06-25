import "./global.css";

import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import AutoScale from "@/components/AutoScale";
import Index from "./pages/Index";
import TropismProgram from "./pages/TropismProgram";
import CameraLive from "./pages/CameraLive";
import ProgressTropism from "./pages/ProgressTropism";
import ExperimentSummary from "./pages/ExperimentSummary";
import Gallery from "./pages/Gallery";
import NotFound from "./pages/NotFound";

const queryClient = new QueryClient();

export const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <BrowserRouter>
        <AutoScale>
          <Routes>
            <Route path="/" element={<Index />} />
            <Route path="/tropism" element={<TropismProgram />} />
            <Route path="/live" element={<CameraLive />} />
            <Route path="/progress-tropism" element={<ProgressTropism />} />
            <Route path="/summary" element={<ExperimentSummary />} />
            <Route path="/gallery" element={<Gallery />} />
            {/* ADD ALL CUSTOM ROUTES ABOVE THE CATCH-ALL "*" ROUTE */}
            <Route path="*" element={<NotFound />} />
          </Routes>
        </AutoScale>
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);
